import numpy as np
import torch
import pyaudio
import matplotlib.pyplot as plt
import time

# -------------------------
# Config
# -------------------------
DEVICE_KEYWORD = "MCHStreamer Multi-channels"
C = 343.0
CH = 16
FS = 48000
CHUNK = 1600
NFFT = 8192

# Beamforming freq band (paper used 2-8 kHz; keep same idea)
F_MIN = 2000.0
F_MAX = 8000.0

# Functional beamformer sharpness (q): tune (8, 16, 32...)
FUNC_Q = 8.0

EMA_ALPHA = 0.2   # 小さいほど滑らか（0.1〜0.3が良い）
EPS = 1e-12



# -------------------------
# Mic positions (4x4 grid, 42mm spacing) - your code 그대로
# -------------------------
d = 0.042
MIC_POS = np.array([
    [-0.5*d, -1.5*d, 0], [-1.5*d, -1.5*d, 0], [-0.5*d, -0.5*d, 0], [-1.5*d, -0.5*d, 0],
    [-0.5*d,  0.5*d, 0], [-1.5*d,  0.5*d, 0], [-0.5*d,  1.5*d, 0], [-1.5*d,  1.5*d, 0],
    [ 1.5*d,  1.5*d, 0], [ 0.5*d,  1.5*d, 0], [ 1.5*d,  0.5*d, 0], [ 0.5*d,  0.5*d, 0],
    [ 1.5*d, -0.5*d, 0], [ 0.5*d, -0.5*d, 0], [ 1.5*d, -1.5*d, 0], [ 0.5*d, -1.5*d, 0],
], dtype=np.float32)

# -------------------------
# Utils
# -------------------------
def find_device(pa):
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info.get("name", "")
        max_ch = info.get("maxInputChannels", 0)
        fs = int(info.get("defaultSampleRate", 0))
        if DEVICE_KEYWORD in name and max_ch == CH and fs == FS:
            return i, info
    return None, None

def blackman_harris_window(N: int, device):
    """
    SciPy無しでBlackman-Harris(4-term)窓を作る。
    """
    n = torch.arange(N, device=device, dtype=torch.float32)
    a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
    w = (a0
         - a1*torch.cos(2*np.pi*n/(N-1))
         + a2*torch.cos(4*np.pi*n/(N-1))
         - a3*torch.cos(6*np.pi*n/(N-1)))
    return w

def compute_csm_single_frame(x_t: torch.Tensor, win: torch.Tensor):
    """
    x_t: (T, CH) float32
    win: (NFFT,)
    1フレームだけでCSMを作る最小版（平均なし）。
    return:
      X: (CH, F) complex64
      S: (F, CH, CH) complex64  (CSM)
      freqs: (F,) float32
    """
    # window
    xw = x_t[:NFFT, :] * win[:, None]  # (NFFT, CH)

    # rFFT over time, per channel
    X = torch.fft.rfft(xw.T, n=NFFT, dim=-1)  # (CH, F)

    # CSM: S[f] = X[:,f] X[:,f]^H
    # outer product for each frequency bin
    Xf = X.permute(1, 0).contiguous()  # (F, CH)
    S = Xf[:, :, None] * torch.conj(Xf[:, None, :])  # (F, CH, CH)
    freqs = torch.fft.rfftfreq(NFFT, d=1.0/FS).to(x_t.device)
    return X, S, freqs

def make_plane_grid(nx=64, ny=64, z=1.5, xlim=(-2.5, 2.5), ylim=(-2.5, 2.5), device="cpu"):
    """
    前方1.5mの平面上に等間隔グリッド（64x64）を作る。
    ※論文は合成グリッド(2081点)だが、まずは等間隔でOK。
    """
    xs = torch.linspace(xlim[0], xlim[1], nx, device=device)
    ys = torch.linspace(ylim[0], ylim[1], ny, device=device)
    Y, X = torch.meshgrid(ys, xs, indexing="ij")  # (ny,nx)
    Z = torch.full_like(X, float(z))
    P = torch.stack([X, Y, Z], dim=-1).reshape(-1, 3)  # (G,3)
    return P, (ny, nx)

def steering_vectors(grid_pts: torch.Tensor, mic_pos: torch.Tensor, freqs: torch.Tensor, c=C):
    """
    grid_pts: (G,3)
    mic_pos: (CH,3)
    freqs: (F,)
    return omega: (F, G, CH) complex64
    """
    # distances: (G, CH)
    diff = grid_pts[:, None, :] - mic_pos[None, :, :]   # (G,CH,3)
    dist = torch.linalg.norm(diff, dim=-1)              # (G,CH)
    tau = dist / c                                      # (G,CH)

    # phase: -j 2π f tau
    # (F,1,1)*(1,G,CH) -> (F,G,CH)
    phase = -2j * np.pi * freqs[:, None, None] * tau[None, :, :]
    omega = torch.exp(phase)  # (F,G,CH)

    # optional normalization (keep stable)
    omega = omega / (torch.sqrt(torch.tensor(mic_pos.shape[0], device=mic_pos.device, dtype=torch.float32)) + EPS)
    return omega.to(torch.complex64)

def functional_beamform(S: torch.Tensor, omega: torch.Tensor, q=16.0):
    """
    S: (F, CH, CH)
    omega: (F, G, CH)
    return P: (F, G) real32  (normalized power)
    P(x) = ( (w^H S w) / tr(S) )^q
    ※論文の式に合わせた形 :contentReference[oaicite:2]{index=2}
    """
    # w^H S w
    # (F,G,CH) x (F,CH,CH) x (F,G,CH)
    # einsum: conj(w)_c * S_cd * w_d
    num = torch.einsum("fgc,fcd,fgd->fg", torch.conj(omega), S, omega)  # (F,G) complex
    num = torch.real(num)

    tr = torch.real(torch.einsum("fii->f", S))  # (F,)
    denom = tr[:, None] + EPS

    p = torch.clamp(num / denom, min=0.0)
    p = torch.pow(p, q)
    return p

def map_postprocess(power_fg: torch.Tensor, out_shape):
    # power_fg already band-limited (F_band, G)
    pb = power_fg.mean(dim=0)  # (G,)

    pb = torch.clamp(pb, min=EPS)
    Lp = 10.0 * torch.log10(pb)

    a = torch.exp(Lp - torch.max(Lp))
    img = a.reshape(out_shape)
    return img

# -------------------------
# Main
# -------------------------
def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("device:", device)

    pa = pyaudio.PyAudio()
    dev_id, info = find_device(pa)
    if dev_id is None:
        print("[ERROR] device not found:", DEVICE_KEYWORD)
        return

    stream = pa.open(format=pyaudio.paInt32,
                     channels=CH,
                     rate=FS,
                     input=True,
                     frames_per_buffer=CHUNK,
                     input_device_index=dev_id)

    mic_pos_t = torch.from_numpy(MIC_POS).to(device)
    win = blackman_harris_window(NFFT, device)

    grid_pts, out_shape = make_plane_grid(
        nx=64, ny=64, z=1.5,
        xlim=(-2.5, 2.5), ylim=(-2.5, 2.5),
        device=device
    )

    # 周波数帯制限
    freqs_all = torch.fft.rfftfreq(NFFT, d=1.0/FS).to(device)
    band = (freqs_all >= F_MIN) & (freqs_all <= F_MAX)
    freqs_band = freqs_all[band]

    omega = steering_vectors(grid_pts, mic_pos_t, freqs_band, c=C)

    # リングバッファ（最新8192サンプル保持）
    ring = torch.zeros((NFFT, CH), device=device, dtype=torch.float32)
    filled = 0

    S_ema = None

    plt.ion()
    fig, ax = plt.subplots()
    im = None

    # ===== 動的ノイズ床 =====
    noise_rms = None
    NOISE_ALPHA = 0.02      # ノイズ床更新速度（小さいほど安定）
    SNR_RATIO_TH = 1.8      # 何倍で「音あり」扱いにするか
    MIN_NOISE_FLOOR = 1e-6  # ゼロ除算防止

    try:
        while True:
            t0 = time.perf_counter()

            # -------- 33ms取り込み --------
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_i32 = np.frombuffer(data, dtype=np.int32).reshape(-1, CH)
            a = torch.from_numpy(audio_i32.astype(np.float32)).to(device) / (2**31)

            n = a.shape[0]

            # -------- リング更新 --------
            if n >= NFFT:
                ring[:] = a[-NFFT:]
                filled = NFFT
            else:
                ring = torch.roll(ring, -n, dims=0)
                ring[-n:] = a
                filled = min(NFFT, filled + n)

            if filled < NFFT:
                continue  # 起動直後のみ

            # ===== RMS計算 =====
            frame = ring  # (NFFT, CH)
            rms = torch.sqrt(torch.mean(frame**2) + 1e-12)

            # ===== ノイズ床初期化 =====
            if noise_rms is None:
                noise_rms = rms.detach()
            else:
                # 小さい時だけノイズ床を更新（発話が混ざらないように）
                if rms < noise_rms * 1.2:
                    noise_rms = (1 - NOISE_ALPHA) * noise_rms + NOISE_ALPHA * rms.detach()

            # ===== SNR判定 =====
            noise_floor = torch.clamp(noise_rms, min=MIN_NOISE_FLOOR)
            is_active = (rms > noise_floor * SNR_RATIO_TH)


            if not is_active:
                # 無音：マップ更新しない
                if im is not None:
                    # 少しずつ減衰させると自然
                    im.set_data(im.get_array() * 0.95)
                    plt.pause(0.001)
                continue

            # -------- 最新8192で計算 --------
            xw = ring * win[:, None]
            X = torch.fft.rfft(xw.T, n=NFFT, dim=-1)
            S = (X.permute(1,0)[:,:,None] *
                 torch.conj(X.permute(1,0)[:,None,:]))

            S_band = S[band]

            # EMA
            if S_ema is None:
                S_ema = S_band
            else:
                S_ema = EMA_ALPHA * S_band + (1 - EMA_ALPHA) * S_ema

            Pfg = functional_beamform(S_ema, omega, q=FUNC_Q)
            amap = map_postprocess(Pfg, out_shape)  # torch (ny,nx), max=1
            mean_val = torch.mean(amap)
            # 例：平均が大きい（= ぼやけてる）なら棄却
            if mean_val > 0.25:
                # 方向が決まってない（拡散/ノイズ）とみなして更新停止
                continue
            amap_cpu = amap.detach().cpu().numpy()

            if im is None:
                im = ax.imshow(amap_cpu, origin="lower")
                plt.colorbar(im, ax=ax)
            else:
                im.set_data(amap_cpu)

            plt.pause(0.001)

            t1 = time.perf_counter()
            print(f"loop ms: {(t1 - t0)*1000:.1f}")

    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

if __name__ == "__main__":
    main()