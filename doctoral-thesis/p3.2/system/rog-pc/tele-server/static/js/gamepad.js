/**
 * ゲームパッド -> 操作 UI サーバ。
 *
 * 沿用元からの変更:
 *   - 右スティック（camera）を送らない。頭部の指令元は vlm-server の VLM
 *   - 速度スケーリングは送らない。robot-pc の rover 側で掛ける
 *     （無線を越える構成では、止める処理も機体側に無いと効かない）
 */
window.STATUS = { speed: 0, rotate: 0 };

const COMMAND_INTERVAL = 100;   // 10 Hz
const INACTIVITY_LIMIT = 3000;  // 無操作で送信停止（機体側にも watchdog あり）
const DEADZONE = 0.05;
// 割り当てのある 2 個だけ。X = 腕を下ろす / Y = 腕を上げる。
const buttonMap = { 2: "X", 3: "Y" };

let GAMEPAD_CONNECTED = false, GAMEPAD_INDEX = null;
let intervalId = null, stopTimeoutId = null, prevButtons = [];
let lastSpeedRotate = [null, null];

window.addEventListener("gamepadconnected", (e) => {
    console.info("Gamepad connected:", e.gamepad.id);
    if (!GAMEPAD_CONNECTED) {
        GAMEPAD_CONNECTED = true;
        GAMEPAD_INDEX = e.gamepad.index;
        queryGamepadLoop();
    }
});
window.addEventListener("gamepaddisconnected", (e) => {
    if (GAMEPAD_INDEX === e.gamepad.index) {
        GAMEPAD_CONNECTED = false; GAMEPAD_INDEX = null;
        stopSending();
    }
});

function applyDeadzone(v) { return Math.abs(v) < DEADZONE ? 0 : v; }

function queryGamepadLoop() {
    if (!GAMEPAD_CONNECTED) return;
    const gp = navigator.getGamepads()[GAMEPAD_INDEX];
    if (gp) {
        STATUS.speed = applyDeadzone(parseFloat((gp.axes[1] * -1).toFixed(4)));
        STATUS.rotate = applyDeadzone(parseFloat((gp.axes[0] * -1).toFixed(4)));
        updateStatusView();

        // **「変化したか」ではなく「倒れているか」で送信を延命する。**
        // 沿用元は変化だけで判定していたが、あちらは右スティック（頭部）も
        // 読んでいて、手が触れている限り微小な変化が絶えず入るので計時器が
        // 途切れなかった。p3.2 は右スティックを使わない（頭部は vlm-server）ので、
        // その延命源が無い。変化だけで見ると、左スティックを倒したまま
        // 保持したときに軸の値が動かず（満舵は正確に ±1.0000 で張り付く）、
        // INACTIVITY_LIMIT が切れて **全速前進のまま 3 秒で停止し、
        // スティックを動かすまで復帰しない。**
        // 倒れている間は毎フレーム延ばし、中央へ戻したら延長を止める
        // （戻した瞬間からは 10 Hz が [0,0] を流し、3 秒後に stopSending が
        // 最後の [0,0] を送って畳む）。
        if (STATUS.speed !== 0 || STATUS.rotate !== 0
            || STATUS.speed !== lastSpeedRotate[0] || STATUS.rotate !== lastSpeedRotate[1]) {
            onUserInput();
        }
        lastSpeedRotate = [STATUS.speed, STATUS.rotate];

        gp.buttons.forEach((button, index) => {
            const was = prevButtons[index] || false;
            const now = button.pressed;
            const el = document.getElementById("btn" + (buttonMap[index] || ""));
            if (el) el.classList.toggle("active", now);

            if (now && !was) {
                // Y = 腕を上げる / X = 腕を下ろす。角度は robot-pc 側で決まる。
                if (index === 3) sendCommand("arm", "up");
                else if (index === 2) sendCommand("arm", "down");
            }
            prevButtons[index] = now;
        });
    }
    requestAnimationFrame(queryGamepadLoop);
}

function updateStatusView() {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.innerText = v; };
    set("speed", STATUS.speed); set("rotate", STATUS.rotate);
}

function startSending() {
    if (intervalId) return;
    intervalId = setInterval(() => {
        sendCommand('twist', [STATUS.speed, STATUS.rotate]);
    }, COMMAND_INTERVAL);
}

function stopSending() {
    clearInterval(intervalId); intervalId = null;
    sendCommand('twist', [0, 0]);
}

function onUserInput() {
    startSending();
    clearTimeout(stopTimeoutId);
    stopTimeoutId = setTimeout(stopSending, INACTIVITY_LIMIT);
}

// ゲームパッドが無くても画面のボタンで腕を動かせるようにする
window.addEventListener("load", () => {
    const y = document.getElementById("btnY");
    const x = document.getElementById("btnX");
    if (y) y.addEventListener("click", () => sendCommand("arm", "up"));
    if (x) x.addEventListener("click", () => sendCommand("arm", "down"));
});
