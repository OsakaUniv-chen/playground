from tqdm import tqdm
import torch
from torch.utils.data import DataLoader
from utils_all_load import *

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
            return temp_data[:self.args.clip_len, ...].contiguous(), temp_data[self.args.clip_len:, ...].contiguous(), temp_orig_sm_data[self.args.clip_len:, ...].contiguous(), temp_orig_sm_data[self.args.clip_len-1:self.args.clip_len, ...].repeat(self.args.pred_len, 1, 1, 1).contiguous()

def peak_dist(tensor1, tensor2):
    max_idx1 = torch.argmax(tensor1.view(*tensor1.shape[:-2], -1), dim=-1)
    max_idx2 = torch.argmax(tensor2.view(*tensor2.shape[:-2], -1), dim=-1)

    rows1, cols1 = max_idx1 // 64, max_idx1 % 64
    rows2, cols2 = max_idx2 // 64, max_idx2 % 64
    distance = torch.sqrt((rows1 - rows2).pow(2) + (cols1 - cols2).pow(2)).double()
    return distance

def kernel_sum_frame(batch, kernel_size=3):
    kernel = torch.ones(1, 1, kernel_size, kernel_size, device=batch.device)
    
    output_batch = torch.zeros_like(batch)
    pad_size = kernel_size // 2
    
    for i in range(batch.shape[0]):
        for j in range(batch.shape[1]):
            frame = batch[i, j].unsqueeze(0).unsqueeze(0)
            
            padded_frame = F.pad(frame, [pad_size]*4, mode='constant', value=0)
            summed_frame = F.conv2d(padded_frame, kernel, bias=None)
            
            ones_frame = torch.ones_like(frame)
            padded_ones = F.pad(ones_frame, [pad_size]*4, mode='constant', value=0)
            valid_neighbors = F.conv2d(padded_ones, kernel, bias=None)
            
            output_batch[i, j] = (summed_frame / valid_neighbors).squeeze()
    
    return output_batch

rank = 0
args = parse_args()

"""
RIKEN 3F
'2024-10-23-17_25_43', '2024-10-23-17_35_17', '2024-10-23-17_44_24', '2024-10-23-18_09_11', '2024-10-23-18_14_03', '2024-10-23-18_23_30', '2024-10-24-15_24_23', '2024-10-24-15_27_18', '2024-10-24-15_51_45', '2024-10-24-16_09_19', '2024-10-24-16_26_21', '2024-10-24-16_36_46', '2024-10-24-16_45_36', '2024-10-24-16_54_21', '2024-10-28-15_34_51', '2024-10-28-15_48_01', '2024-10-30-16_43_01', '2024-10-30-17_00_09', '2024-10-30-17_19_47', '2024-10-30-18_13_39', '2024-10-30-18_20_48', '2024-10-30-18_26_49', '2024-10-30-18_35_47', '2024-10-31-12_26_41', '2024-10-31-14_42_16', '2024-10-31-16_47_58', '2024-10-31-16_55_55', '2024-10-31-17_02_40', 

RIKEN 1F
'2024-12-17-11_42_30', '2024-12-17-11_48_19', '2024-12-17-11_54_00', '2024-12-17-11_59_51', '2024-12-17-12_09_33', '2024-12-17-12_16_57', '2024-12-17-12_27_17', '2024-12-17-12_34_26', '2024-12-17-12_40_29', '2024-12-18-11_25_36', '2024-12-18-11_32_12', '2024-12-18-11_39_37', '2024-12-18-11_47_30', '2024-12-18-11_52_15', '2024-12-18-11_55_08', '2024-12-18-12_07_29', '2024-12-18-12_18_02', '2024-12-18-12_27_18', '2024-12-18-12_30_00', '2024-12-18-12_36_52', '2024-12-19-11_31_29', '2024-12-19-11_35_33', '2024-12-19-11_40_36', '2024-12-19-11_47_43', '2024-12-19-11_57_05', '2024-12-19-12_09_46', '2024-12-19-12_21_07', '2024-12-25-11_40_43', '2024-12-25-11_46_03', '2024-12-25-11_56_08', '2024-12-25-12_06_16', '2024-12-25-12_15_37', '2024-12-25-12_21_32', '2024-12-26-11_32_39', '2024-12-26-11_38_00', '2024-12-26-11_46_00', '2024-12-26-11_54_38', '2024-12-27-11_34_15', '2024-12-27-11_48_04', '2024-12-27-11_51_54', '2024-12-27-11_55_32', '2024-12-27-12_07_15', '2024-12-27-12_13_44', '2024-12-27-12_19_14', '2024-12-27-12_23_19', '2024-12-27-12_29_20', '2024-12-27-12_33_34', '2024-12-27-12_40_09', '2024-12-27-12_47_06', 

Nakamura-Lab
'2025-01-16-13_08_04', '2025-01-16-13_56_44', 

TUS
'debate_exp1_topic1', 'debate_exp1_topic2', 'debate_exp1_topic3'
"""

args.exp_name = []
fused_output = False
output_transform = False

data_loader = NPZLoader(args)
print('NPZLoader is ready.')

try:
    if not torch.cuda.is_available():
        raise ValueError('CUDA is not available.')
    
    set_seed(args.seed)

    test_dataset = CustomDataset(data_loader.test_valid_ind, data_loader.test_sm_data, data_loader.test_orig_sm_data, data_loader.test_mods_data, data_loader.skip_frames, args)
    print('Dataset is ready.')
    print('Test dataset length:', len(test_dataset))

    test_loader = DataLoader(
        test_dataset, 
        batch_size=args.bs,
        num_workers=args.num_workers,
        pin_memory=args.pin_memory, 
        sampler=None,
        shuffle=False,
    )
    print('DataLoader is ready.')

    ##################################################
    ############### Model Initialization ##############
    ##################################################
    from simvp import SimVP
    shape_in = (args.clip_len, data_loader.channel_count, args.img_sz, args.img_sz)
    model = SimVP(shape_in, args.pred_len, model_type=args.simvp_type).cuda(rank)
    model = load_model(model, args.weight_path, rank)
    
    print('Model is ready.')
    
    # 'mse': ground truth vs prediction
    # 'mse_baseline': ground truth vs last frame repeted
    # 'mse_with_baseline': prediction vs last frame repeted
    test_metrics = {'mse': 0, 'peak_dist': 0}
    test_metrics_by_time = {'mse': np.array([0,0,0,0], dtype=np.float64), 'peak_dist': np.array([0,0,0,0], dtype=np.float64)}

    model.eval()
    with torch.no_grad():
        loader = test_loader
        loader_name = 'Test'
        
        #peak_distance = torch.empty(0, args.pred_len, dtype=torch.float64)
        #peak_distance_k3 = torch.empty(0, args.pred_len, dtype=torch.float64)
        peak_distance_k5 = torch.empty(0, args.pred_len, dtype=torch.float64)
        #peak_distance_k7 = torch.empty(0, args.pred_len, dtype=torch.float64)
        #peak_distance_k9 = torch.empty(0, args.pred_len, dtype=torch.float64)
        peak_distance_k5_baseline = torch.empty(0, args.pred_len, dtype=torch.float64)

        pbar = tqdm(loader, desc=f'{loader_name} Evaluation', position=0, leave=True)
        for batch_idx, (batch_data, batch_target, batch_orig_sm, batch_orig_last_sm) in enumerate(loader):
            batch_orig_sm = batch_orig_sm.squeeze(2)
            target = batch_target[:, :, 0, :, :] # fused sound map

            if fused_output:
                target = batch_target[:, :, 0, :, :] # fused sound map
                batch_last_sm = batch_data[:, -1:, 0, :, :].repeat(1, args.pred_len, 1, 1)
            else:
                target = batch_orig_sm # original sound map
                batch_last_sm = batch_orig_last_sm.squeeze(2)

            batch_data = batch_data.cuda(rank).contiguous()
            target = target.cuda(rank).contiguous()
            batch_orig_sm = batch_orig_sm.cuda(rank).contiguous()
            batch_last_sm = batch_last_sm.cuda(rank).contiguous()

            batch_output = model(batch_data)[:, :, 0, :, :] # only use sound map
            
            mse_by_time = MSE_Loss(batch_output, target, reduction='none').double().sum(dim=(2, 3))
            
            if fused_output and output_transform:
                peak_dist_output = torch.clamp((batch_output-(1-args.sm_ratio))/args.sm_ratio, 0, 1)
            else:
                peak_dist_output = batch_output

            #peak_dist_by_time = peak_dist(peak_dist_output, batch_orig_sm)
            #peak_dist_k3_by_time = peak_dist(kernel_sum_frame(peak_dist_output, kernel_size=3), kernel_sum_frame(batch_orig_sm, kernel_size=3))
            peak_dist_k5_by_time = peak_dist(kernel_sum_frame(peak_dist_output, kernel_size=5), kernel_sum_frame(batch_orig_sm, kernel_size=5))
            #peak_dist_k7_by_time = peak_dist(kernel_sum_frame(peak_dist_output, kernel_size=7), kernel_sum_frame(batch_orig_sm, kernel_size=7))
            #peak_dist_k9_by_time = peak_dist(kernel_sum_frame(peak_dist_output, kernel_size=9), kernel_sum_frame(batch_orig_sm, kernel_size=9))
            peak_dist_k5_baseline_by_time = peak_dist(kernel_sum_frame(batch_last_sm, kernel_size=5), kernel_sum_frame(batch_orig_sm, kernel_size=5))

            test_metrics['mse'] += mse_by_time.sum().item()
            #test_metrics['peak_dist'] += peak_dist_by_time.sum().item()
            
            #peak_distance = torch.cat((peak_distance, peak_dist_by_time.cpu()), dim=0)
            #peak_distance_k3 = torch.cat((peak_distance_k3, peak_dist_k3_by_time.cpu()), dim=0)
            peak_distance_k5 = torch.cat((peak_distance_k5, peak_dist_k5_by_time.cpu()), dim=0)
            #peak_distance_k7 = torch.cat((peak_distance_k7, peak_dist_k7_by_time.cpu()), dim=0)
            #peak_distance_k9 = torch.cat((peak_distance_k9, peak_dist_k9_by_time.cpu()), dim=0)
            peak_distance_k5_baseline = torch.cat((peak_distance_k5_baseline, peak_dist_k5_baseline_by_time.cpu()), dim=0)

            pbar.update(1)
        pbar.close()

    test_metrics['mse'] /= (len(test_loader.dataset) * args.pred_len * args.img_sz**2)
    #test_metrics['peak_dist'] /= (len(test_loader.dataset) * args.pred_len)
    
    threshold_peakD = 5

    #peak_distance = (peak_distance < threshold_peakD).float().mean()
    #peak_distance_k3_ratio = (peak_distance_k3 < threshold_peakD).float().mean()
    peak_distance_k5_ratio = (peak_distance_k5 < threshold_peakD).float().mean()
    #peak_distance_k7_ratio = (peak_distance_k7 < threshold_peakD).float().mean()
    #peak_distance_k9_ratio = (peak_distance_k9 < threshold_peakD).float().mean()
    peak_distance_k5_baseline_ratio = (peak_distance_k5_baseline < threshold_peakD).float().mean()

    peak_distance_k5_ratio_by_time = (peak_distance_k5 < threshold_peakD).float().mean(dim=0)
    peak_distance_k5_ratio_by_time_formatted = [f"{x:.5f}" for x in peak_distance_k5_ratio_by_time.tolist()]
    
    print(f"Test MSE: {test_metrics['mse']:.6f}")
    #print(f"Test Peak Dist: {test_metrics['peak_dist']:.3f}")
    

    #print(f"Peak Distance Ratio: {peak_distance:.5f}")
    #print(f"Peak Distance K3 Ratio: {peak_distance_k3_ratio:.5f}")
    print(f"Peak Distance K5 Ratio: {peak_distance_k5_ratio:.5f}, {peak_distance_k5_ratio_by_time_formatted}")
    #print(f"Peak Distance K7 Ratio: {peak_distance_k7_ratio:.5f}")
    #print(f"Peak Distance K9 Ratio: {peak_distance_k9_ratio:.5f}")

    print(f"Peak Distance K5 Baseline Ratio: {peak_distance_k5_baseline_ratio:.5f}")


except KeyboardInterrupt:
    print("Interrupted by user. Cleaning up...")
finally:
    print("Over.")


