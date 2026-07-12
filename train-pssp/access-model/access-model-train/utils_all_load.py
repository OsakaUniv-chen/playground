import os
import cv2
import json
import random
import argparse
import numpy as np
from scipy.linalg import sqrtm

import torch
import torch.distributed as dist
from torch.utils.data import Dataset
import torch.nn.functional as F


class NPZLoader:
    def __init__(self, args):
        self.root_path = args.npz_path
        self.train_sm_data, self.train_orig_sm_data, self.train_mods_data = [], [], []
        self.test_sm_data, self.test_orig_sm_data, self.test_mods_data = [], [], []
        self.exp_list = args.exp_name
        self.mod_list = args.modality_list
        self.clip_len = args.clip_len
        self.pred_len = args.pred_len
        self.train_ratio = args.train_ratio
        self.img_size = args.img_sz
        self.sm_ratio = args.sm_ratio
        self.method = args.method
        
        self.valid_abc = {
            # abc: [file_name, channel_num, (min_val, max_val)]
            'soundmap': ['soundmap.npy', 1, (0, 1)],
            'camimg': ['camimg.npy', 3, (0, 255)],
            'gray_camimg': ['gray_camimg.npy', 1, (0, 255)],
            'semseg': ['semseg.npy', 4, (0, 1)],
            'depth': ['depth.npy', 1, (0, 20)],
            'normals': ['normals.npy', 3, (0, 255)],
            'edge': ['edge.npy', 1, (0, 255)],
            'human_parts': ['human_parts.npy', 7, (0, 1)],
            'sal': ['sal.npy', 1, (0, 255)],
            'head_pose': ['head_pose.npy', 3, (-180, 180)],
            'eye': ['eye.npy', 1, (0, 100)],
            'mouth': ['mouth.npy', 1, (0, 100)],
            'emotion_valence': ['emotion_valence.npy', 1, (-1, 1)],
            'emotion_arousal': ['emotion_arousal.npy', 1, (-1, 1)],
        }
        
        self.channel_count = 0
        for abc in self.mod_list:
            self.channel_count += self.valid_abc[abc][1]

        if args.fps > 30 or args.fps < 1:
            raise ValueError('Invalid fps value. Must be between 1 and 30.')
        
        if self.method == 'vae':
            self.skip_frames = 1 # no skip
        else:
            self.skip_frames = 30 // args.fps # default cam fps=30

        self.get_index() # generate train/test valid index
        self.load_data() # load data into memory

    
    def locate_npz_files(self, abc):
        """ Locate all available npz files of mod:abc in the root path. """
        if abc not in self.valid_abc:
            raise ValueError(f"Invalid abc: {abc}")

        npz_files = []
        for dirpath, _, filenames in os.walk(self.root_path):
            for file in filenames:
                if file == self.valid_abc[abc][0]:
                    npz_files.append(os.path.join(dirpath, file))
        npz_files.sort()
        
        return npz_files


    def get_index(self):
        self.npz_files = self.locate_npz_files('soundmap')
        if len(self.exp_list) > 0:
            self.npz_files = [file for file in self.npz_files if any(exp in file for exp in self.exp_list)]
        print(self.npz_files)
        print(f'Found {len(self.npz_files)} files for calculating valid index...')
        
        train_idx, self.train_valid_ind = 0, []
        test_idx, self.test_valid_ind = 0, []

        for index, file_path in enumerate(self.npz_files):
            try:
                temp_data = np.load(file_path, mmap_mode='r')

                data_num = len(temp_data)
                train_num = int(data_num * self.train_ratio)
                test_num = data_num - train_num
                print(f'Processing {file_path} with {data_num} frames...  ', 'train:', train_num, 'test:', test_num)

                adjusted_train_num = train_num - (self.clip_len + self.pred_len - 1) * self.skip_frames if self.method != 'vae' else train_num
                adjusted_test_num = test_num - (self.clip_len + self.pred_len - 1) * self.skip_frames if self.method != 'vae' else test_num

                for i in range(train_num if self.method == 'vae' else adjusted_train_num):
                    self.train_valid_ind.append(train_idx + i)
                train_idx += train_num

                for i in range(test_num if self.method == 'vae' else adjusted_test_num):
                    self.test_valid_ind.append(test_idx + i)
                test_idx += test_num

            except Exception as e:
                print(f"Error loading {file_path}: {e}")

        print('train data count:', len(self.train_valid_ind), 'test data count:', len(self.test_valid_ind))


    def load_data(self):
        for _, file_path in enumerate(self.npz_files):
            folder_path = os.path.dirname(file_path)
            temp_sm_data = np.load(file_path)
            temp_orig_sm_data = temp_sm_data.copy()

            if self.sm_ratio != 1.0:
                temp_gray = np.load(folder_path + '/gray_camimg.npy')
                temp_gray = (temp_gray / 255.0).astype(np.float32)
                temp_sm_data = self.sm_ratio * temp_sm_data + (1-self.sm_ratio) * temp_gray

            data_num = len(temp_sm_data)
            train_num = int(data_num * self.train_ratio)

            self.train_sm_data.append(temp_sm_data[:train_num])
            self.test_sm_data.append(temp_sm_data[train_num:])
            self.train_orig_sm_data.append(temp_orig_sm_data[:train_num])
            self.test_orig_sm_data.append(temp_orig_sm_data[train_num:])
            print(f'Loaded sound map from {folder_path} with {data_num} frames.')
            
            if len(self.mod_list) == 1: # sm only case
                continue
            else:
                temp_mods_data = []
                for abc in self.mod_list:
                    if abc != 'soundmap':
                        temp_mods_data.append(np.load(folder_path + f'/{abc}.npy'))
                        print(f'Loaded {abc} from {folder_path} with {data_num} frames.')
                temp_mods_data = np.concatenate(temp_mods_data, axis=1)

            self.train_mods_data.append(temp_mods_data[:train_num])
            self.test_mods_data.append(temp_mods_data[train_num:])

        self.train_sm_data = torch.from_numpy(np.concatenate(self.train_sm_data, axis=0)).to(dtype=torch.float32)
        self.test_sm_data = torch.from_numpy(np.concatenate(self.test_sm_data, axis=0)).to(dtype=torch.float32)
        self.train_orig_sm_data = torch.from_numpy(np.concatenate(self.train_orig_sm_data, axis=0)).to(dtype=torch.float32)
        self.test_orig_sm_data = torch.from_numpy(np.concatenate(self.test_orig_sm_data, axis=0)).to(dtype=torch.float32)
        if len(self.mod_list) == 1: # sm only case
            self.train_mods_data = None
            self.test_mods_data = None
        else:
            self.train_mods_data = torch.from_numpy(np.concatenate(self.train_mods_data, axis=0)).to(dtype=torch.uint8)
            self.test_mods_data = torch.from_numpy(np.concatenate(self.test_mods_data, axis=0)).to(dtype=torch.uint8)
    

class CustomDataset(Dataset):
    def __init__(self, valid_data_index, sm_data, orig_sm_data, mods_data, skip_frames, args):
        self.valid_data_index = valid_data_index
        self.sm_data = sm_data
        self.orig_sm_data = orig_sm_data
        self.mods_data = mods_data
        self.skip_frames = skip_frames
        self.args = args

    def __len__(self):
         return len(self.valid_data_index)

    def __getitem__(self, idx):
        idx = self.valid_data_index[idx]

        if self.args.method == 'vae':
            temp_data = self.sm_data[idx].clone()
            if len(self.args.modality_list) > 1:
                temp_data = torch.cat((
                    temp_data, self.mods_data[idx].clone().to(dtype=torch.float32) / 255.0)
                , dim=1)
            return temp_data.contiguous(), 0 # cannot return None in __getitem__
        else:
            temp_data = self.sm_data[idx:idx+(self.args.clip_len+self.args.pred_len-1)*self.skip_frames+1:self.skip_frames].clone()
            temp_orig_sm_data = self.orig_sm_data[idx:idx+(self.args.clip_len+self.args.pred_len-1)*self.skip_frames+1:self.skip_frames].clone()
            if len(self.args.modality_list) > 1:
                temp_data = torch.cat((
                    temp_data, self.mods_data[idx:idx+(self.args.clip_len+self.args.pred_len-1)*self.skip_frames+1:self.skip_frames].clone().to(dtype=torch.float32) / 255.0)
                , dim=1)
            return temp_data[:self.args.clip_len, ...].contiguous(), temp_orig_sm_data[self.args.clip_len:, ...].contiguous()


def MSE_KL_Loss(recon_x, x, mu, logvar, reduction='mean'):
    # Reconstruction loss (MSE)
    criterion = torch.nn.MSELoss(reduction=reduction)
    BCE = criterion(recon_x, x)
    
    # KL divergence (scaled by batch size)
    KLD = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp()) / x.size(0)
    
    return BCE + KLD, [BCE.item(), KLD.item()]


def MSE_Loss(pred, target, reduction='mean'):
    criterion = torch.nn.MSELoss(reduction=reduction)
    loss = criterion(pred, target)

    return loss


def loss_func(*args):
    # args[0] is the loss function name
    if not args:
        raise ValueError('Loss function not provided.')

    func_map = {
        'mse': MSE_Loss,
        'mse_kl': MSE_KL_Loss,
        }
    
    method = args[0]
    func = func_map.get(method)
    
    if func:
        return func(*args[1:])
    else:
        raise ValueError('Unknown loss function.')


def preprocess_batch(method, batch_data, batch_target):
    """ Only used when all mods resized to same size. """
    if method == 'vae':
        # batch_data: {'key': [B,H,W,C], ...}
        batch_data = np.concatenate([batch_data[key] for key in batch_data], axis=3)
        # (B,H,W,C) -> (B,C,H,W)
        batch_data = torch.from_numpy(batch_data).permute(0, 3, 1, 2).float() 
        batch_target = None
    else:
        # batch_data/batch_target: {'key': [B,clip_T/pred_T,H,W,C], ...}
        batch_data = np.concatenate([batch_data[key] for key in batch_data], axis=4)
        batch_target = np.concatenate([batch_target[key] for key in batch_target], axis=4)
        # (B,T,H,W,C) -> (B,T,C,H,W)
        batch_data = torch.from_numpy(batch_data).permute(0, 1, 4, 2, 3).float() 
        batch_target = torch.from_numpy(batch_target).permute(0, 1, 4, 2, 3).float() 

    return batch_data, batch_target


def FlipAndRandomCrop_step1(batch_img, scale=(0.95, 1.0), ratio=(9./10., 10./9.), try_n=20):
    """batch_img: (B, C, H, W) or (B, T, C, H, W)"""
    if batch_img.ndim == 4:
        B, C, H, W = batch_img.shape
    elif batch_img.ndim == 5:
        B, T, C, H, W = batch_img.shape

    # Horizontal Flip Preparation
    if random.random() > 0.5:
        flip_flag = True
    else:
        flip_flag = False

    # Random Crop Preparation
    size = (W, H)
    area = H * W
    random_success = False
    for _ in range(try_n):
        target_area = random.uniform(*scale) * area
        aspect_ratio = random.uniform(*ratio)

        new_width = int(round((target_area * aspect_ratio) ** 0.5))
        new_height = int(round((target_area / aspect_ratio) ** 0.5))

        if new_width <= W and new_height <= H:
            x = random.randint(0, W - new_width)
            y = random.randint(0, H - new_height)
            random_success = True
            break

    if not random_success:
        # Fallback to Central Crop
        in_ratio = float(W) / float(H)
        if in_ratio < min(ratio):
            new_width = W
            new_height = int(round(new_width / min(ratio)))
        elif in_ratio > max(ratio):
            new_height = H
            new_width = int(round(new_height * max(ratio)))
        else:
            new_width = W
            new_height = H

        x = (W - new_width) // 2
        y = (H - new_height) // 2

    return flip_flag, (x, x+new_height, y, y+new_width)


def FlipAndRandomCrop_step2(batch_img, flip_flag, coords):
    if batch_img.ndim == 5:
        B, T, C, H, W = batch_img.shape
        batch_img = batch_img.view(B * T, C, H, W)
        reshape_back = True
    else:
        reshape_back = False

    # Horizontal Flip
    if flip_flag:
        batch_img = batch_img.flip(dims=[3])

    # Random Crop
    cropped_imgs = batch_img[:, :, coords[2]:coords[3], coords[0]:coords[1]]
    resized_imgs = torch.nn.functional.interpolate(
        cropped_imgs, 
        size=(batch_img.shape[3], batch_img.shape[2]), 
        mode='bilinear', align_corners=False)

    if reshape_back:
        resized_imgs = resized_imgs.reshape(B, T, C, H, W)

    return resized_imgs


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def save_model(model, path):
    torch.save(model.state_dict(), path)


def load_model(model, path, rank):
    state_dict = torch.load(
            path,
            map_location=f'cuda:{rank}',
            weights_only=True,
    )
    
    state_dict = {k.replace('module.', ''): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)

    return model


def setup(rank, world_size):
    """
    Initialize the distributed environment.
    """
    if world_size > 1:
        dist.init_process_group(
            backend='nccl',  # Use 'gloo' for CPU training
            init_method='env://',  # Use environment variables for initialization
            rank=rank,
            world_size=world_size
        )
        torch.cuda.set_device(rank)


def cleanup():
    """
    Cleanup the distributed environment.
    """
    if dist.is_initialized():
        dist.destroy_process_group()


def load_and_update_args(args):
    config_file = args.config_path
    try:
        with open(config_file, 'r') as file:
            config = json.load(file)
    except Exception as e:
        raise ValueError(f'Error loading config file: {e}')

    args.config_file = config
    for key, value in config.items():
        if hasattr(args, key):
            setattr(args, key, value)
            print(f'Updated {key} to {value}')

    return args


def create_gaussian_window(window_size: int, sigma: float, channels: int, device: torch.device):
    coords = torch.arange(window_size, dtype=torch.float32, device=device) - window_size // 2
    g = torch.exp(-(coords**2) / (2 * sigma**2))
    g = g / g.sum()
    window = g[:, None] * g[None, :]
    window = window.expand(channels, 1, window_size, window_size)
    return window

def ssim_per_sample_channel(x: torch.Tensor, y: torch.Tensor, window_size: int = 11, sigma: float = 1.5, data_range: float = 1.0) -> torch.Tensor:
    assert x.shape == y.shape, "Input tensors must have the same shape"
    
    if x.dim() == 5:  # Case: (B, T, C, H, W)
        B, T, C, H, W = x.shape
        x = x.reshape(B * T, C, H, W)
        y = y.reshape(B * T, C, H, W)
    else:  # Case: (B, C, H, W)
        B, C, H, W = x.shape
        T = 1  # No temporal dimension
    
    device = x.device
    C1 = (0.01 * data_range) ** 2
    C2 = (0.03 * data_range) ** 2

    window = create_gaussian_window(window_size, sigma, C, device=device)
    padding = window_size // 2

    mu_x = F.conv2d(x, window, padding=padding, groups=C)
    mu_y = F.conv2d(y, window, padding=padding, groups=C)

    mu_x_sq = mu_x * mu_x
    mu_y_sq = mu_y * mu_y
    mu_xy   = mu_x * mu_y

    sigma_x_sq = F.conv2d(x * x, window, padding=padding, groups=C) - mu_x_sq
    sigma_y_sq = F.conv2d(y * y, window, padding=padding, groups=C) - mu_y_sq
    sigma_xy   = F.conv2d(x * y, window, padding=padding, groups=C) - mu_xy

    ssim_map = ((2 * mu_xy + C1) * (2 * sigma_xy + C2)) / ((mu_x_sq + mu_y_sq + C1) * (sigma_x_sq + sigma_y_sq + C2))
    ssim_map_mean = ssim_map.double().mean(dim=(-2, -1))  # (B*T, C)

    if T > 1:
        ssim_map_mean = ssim_map_mean.view(B, T, C).mean(dim=1)  # Averaging over time dimension (B, C)
    
    return ssim_map_mean


def peak_dist(tensor1, tensor2):
    max_idx1 = torch.argmax(tensor1.view(*tensor1.shape[:-2], -1), dim=-1)
    max_idx2 = torch.argmax(tensor2.view(*tensor2.shape[:-2], -1), dim=-1)

    rows1, cols1 = max_idx1 // 64, max_idx1 % 64
    rows2, cols2 = max_idx2 // 64, max_idx2 % 64
    distance = torch.sqrt((rows1 - rows2).pow(2) + (cols1 - cols2).pow(2)).double()

    return distance.mean().item(), torch.mean(distance, dim=0).cpu()


@torch.no_grad()
def extract_i3d_features(video_batch, model, resize=(112, 112)):
    # Resize video frames to match I3D input size
    video_batch = F.interpolate(video_batch, size=(video_batch.shape[2], *resize), mode="trilinear", align_corners=False)
    video_batch = video_batch.repeat(1, 1, 3, 1, 1)
    video_batch = video_batch.permute(0, 2, 1, 3, 4)  # (N, C, T, H, W)
    print(video_batch.shape)
    features = model(video_batch)  # Output shape: (N, feature_dim)
    return features


def FVD_score(i3d_model, x: torch.Tensor, y: torch.Tensor):
    """Fréchet Video Distance (FVD)"""
    print(x.shape)
    fake_features = extract_i3d_features(x, i3d_model)
    real_features = extract_i3d_features(y, i3d_model)
    mu_fake, sigma_fake = fake_features.mean(dim=0), torch.cov(fake_features.T)
    mu_real, sigma_real = real_features.mean(dim=0), torch.cov(real_features.T)

    # Convert tensors to numpy for sqrtm calculation
    mu_fake, sigma_fake = mu_fake.cpu().numpy(), sigma_fake.cpu().numpy()
    mu_real, sigma_real = mu_real.cpu().numpy(), sigma_real.cpu().numpy()

    # Compute Fréchet distance
    mean_diff = np.sum((mu_real - mu_fake) ** 2)
    cov_sqrt = sqrtm(sigma_real @ sigma_fake)

    if np.iscomplexobj(cov_sqrt):
        cov_sqrt = cov_sqrt.real  # Discard imaginary part due to numerical errors

    fvd_score = mean_diff + np.trace(sigma_real + sigma_fake - 2 * cov_sqrt)
    return fvd_score


def plot_samples(args, world_size, epoch, batch_data, batch_target, model, vae_model=None, ddim=None, train_or_test='train', save_or_show='show', channel_info=None, save_folder=None):
    H, W = batch_data.size(-2), batch_data.size(-1)
    modality_list = ["soundmap"]
    
    if args.method == 'vae':
        batch_output, _ = model(batch_data)
        batch_output = batch_output.unsqueeze(1)
        batch_target = batch_data.unsqueeze(1)
    
    elif args.method == 'simvp':
        batch_output = model(batch_data)

    elif args.method == 'vdt':
        B, pred_T, C, H, W = batch_target.shape
        batch_random = torch.randn((B, pred_T, C, H, W), device=batch_data.device)

        if world_size > 1:
            enc_vid = model.module.preprocess_vdt_batch(vae_model, batch_data, batch_random, encode_only=True)
        else:
            enc_vid = model.preprocess_vdt_batch(vae_model, batch_data, batch_random, encode_only=True)

        batch_latent = ddim.generate(model, enc_vid, pred_T)
        batch_latent = batch_latent.reshape(B*pred_T, *batch_latent.shape[-3:])
        batch_output = vae_model.decode(batch_latent)
        batch_output = batch_output.reshape(B, pred_T, *batch_output.shape[-3:])

    batch_data = batch_data.cpu().numpy()
    batch_output = batch_output.cpu().numpy()
    batch_target = batch_target.cpu().numpy()
    
    def visualize_sm(plot_sm):
        filled_sm = (plot_sm * 255).astype(np.uint8)
        filled_sm = np.stack([np.zeros_like(filled_sm), filled_sm, filled_sm], axis=-1)
        return filled_sm
    
    if args.method == 'vae':
        zeros_patch = np.ones((H//8, W*(len(modality_list))*batch_output.shape[1], 3), dtype=np.uint8) * 255
    else:
        zeros_patch = np.ones((H//8, W*(len(modality_list))*batch_output.shape[1]+W*args.clip_len, 3), dtype=np.uint8) * 255

    all_clips = []
    for i in range(batch_target.shape[0]):
        one_clip = []

        if args.method != 'vae':
            input_list = []
            for j in range(batch_data.shape[1]):
                input_list.append(visualize_sm(batch_data[i,j,0,:,:]))
            input_list = np.hstack(input_list)
            one_clip.append(np.vstack((input_list, np.zeros_like(input_list))))

        sm_list_target, sm_list_output = [], []
        for j in range(batch_target.shape[1]):
            sm_list_target.append(visualize_sm(batch_target[i,j,0,:,:]))
            sm_list_output.append(visualize_sm(batch_output[i,j,0,:,:]))
        one_clip.append(np.vstack((np.hstack(sm_list_target), np.hstack(sm_list_output))))
        
        channel_idx = 1
        for mod in modality_list[1:]:
            mod_channel = channel_info[mod][1]
            new_channel_idx = channel_idx + mod_channel
            mod_list_target, mod_list_output = [], []
            for j in range(batch_target.shape[1]):
                true_img = np.transpose(batch_target[i,j,channel_idx:new_channel_idx,:,:], (1, 2, 0))
                pred_img = np.transpose(batch_output[i,j,channel_idx:new_channel_idx,:,:], (1, 2, 0))
                
                if mod in ['semseg', 'human_parts']:
                    true_img = np.expand_dims(np.argmax(true_img, axis=-1), axis=-1) / (mod_channel - 1)
                    pred_img = np.expand_dims(np.argmax(pred_img, axis=-1), axis=-1) / (mod_channel - 1)

                true_img = np.clip(true_img*255, 0, 255).astype(np.uint8)
                pred_img = np.clip(pred_img*255, 0, 255).astype(np.uint8)

                if mod not in ['camimg',  'normals', 'head_pose']:
                    true_img = np.repeat(true_img, 3, axis=2)
                    pred_img = np.repeat(pred_img, 3, axis=2)

                mod_list_target.append(true_img)
                mod_list_output.append(pred_img)
            
            one_clip.append(np.vstack((
                np.hstack(mod_list_target), np.hstack(mod_list_output)
            )))

            channel_idx = new_channel_idx

        one_clip = np.hstack(one_clip)
        one_clip = np.vstack((one_clip, zeros_patch))
        all_clips.append(one_clip)

    all_clips = np.vstack(all_clips)
    all_clips = cv2.resize(all_clips, None, fx=1, fy=1, interpolation=cv2.INTER_AREA)
    
    if save_or_show == 'save':
        if save_folder is None:
            output_dir = f'./results/plots/{args.config}'
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            cv2.imwrite(f'{output_dir}/{args.config}_{train_or_test}_epoch{epoch}.jpg', all_clips)
    
        else:
            output_dir = f'./results/infer/{args.config}'
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            cv2.imwrite(f'{output_dir}/{args.config}_{train_or_test}_infer{save_folder}.jpg', all_clips)
            
    elif save_or_show == 'show':
        cv2.imshow(f'{train_or_test} samples (up: true, bottom: pred)', all_clips)


class EarlyStopping:
    def __init__(self, patience=10, verbose=True, delta=0):
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.inf
        self.delta = delta

    def __call__(self, val_loss, model, args):
        score = -val_loss

        if self.best_score is None:
            self.best_score = score
            if args.save_weight:
                self.save_checkpoint(val_loss, model, args.weight_path)
        elif score < self.best_score + self.delta:
            self.counter += 1
            if self.verbose:
                print(f'EarlyStopping counter: {self.counter} out of {self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            if args.save_weight:
                self.save_checkpoint(val_loss, model, args.weight_path)
            self.counter = 0

    def save_checkpoint(self, val_loss, model, path):
        '''Saves model when validation loss decrease.'''
        if self.verbose:
            print(f'Validation loss decreased ({self.val_loss_min:.6f} --> {val_loss:.6f}).  Saving model ...')
        save_model(model, path)
        self.val_loss_min = val_loss


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('--method', type=str, default='simvp', choices=['vae', 'simvp', 'vdt'])
    
    parser.add_argument('--npz_path', type=str, default='../new_processed_data2')
    parser.add_argument('--exp_name', type=list, default=[], help='[] for all exp, or specify the exp name: debate_exp1_topic1, ...')
    parser.add_argument(
        '--modality_list', 
        type=list, 
        default=['soundmap', 'semseg', 'depth', 'normals'], 
        help='always keep soundmap the first mod',
        choices=[
            'soundmap', 'camimg',
            'semseg', 'depth', 'normals', 'edge', 'human_parts', 'sal',
            'head', 'head_pose', 'eye', 'mouth', 'emotion_valence', 'emotion_arousal',
        ], 
    )

    parser.add_argument('--clip_len', type=int, default=10)
    parser.add_argument('--pred_len', type=int, default=4)
    parser.add_argument('--train_ratio', type=float, default=0.9)
    parser.add_argument('--fps', type=int, default=2, help='time interval 0.5s')

    parser.add_argument('--bs', type=int, default=4)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--weight_decay', type=float, default=0.01)
    parser.add_argument('--eta_min', type=float, default=0.000001)
    parser.add_argument('--epochs', type=int, default=1)
    parser.add_argument('--img_sz', type=int, default=64, help='64, 128, 224')

    parser.add_argument('--sm_ratio', type=float, default=1.0)

    parser.add_argument('--num_workers', type=int, default=4)
    parser.add_argument('--pin_memory', type=bool, default=True)
    parser.add_argument('--save_weight', type=bool, default=True)
    parser.add_argument('--seed', type=int, default=42, help='Random seed for reproducibility')
    parser.add_argument('--data_aug', type=bool, default=True)

    # VDT specific
    parser.add_argument('--vae_weight', type=str, default=None)
    parser.add_argument('--load_vae', type=bool, default=True)
    parser.add_argument('--vdt_type', type=str, default='vdt_s', choices=['vdt_s', 'vdt_l'])
    
    # SimVP specific
    parser.add_argument('--simvp_type', type=str, default='gsta', choices=['incepu', 'gsta', 'tau'])

    parser.add_argument('--config', type=str, default=None)
    parser.add_argument('--rank', type=int, default=0)
    args = parser.parse_args()
    args.weight_path = f'./results/config_{args.config}.pt'
    args.config_path = f'./results/configs/{args.config}.json'
    
    args = load_and_update_args(args)
    if args.load_vae:
        args.vae_weight = f'./results/config_{args.vae_weight}.pt'

    return args
