from tqdm import tqdm
import matplotlib
matplotlib.use('Agg')

import torch
import torch.multiprocessing as mp
from torch.utils.data import DataLoader, DistributedSampler
from torch.nn.parallel import DistributedDataParallel as DDP
torch.multiprocessing.set_sharing_strategy('file_system')

from utils_all_load import *
import wandb

def train(rank, world_size, args, data_loader):
    try:
        if not torch.cuda.is_available():
            raise ValueError('CUDA is not available.')
        
        set_seed(args.seed)
        setup(rank, world_size)
        early_stop_tensor = torch.tensor([0], device=f'cuda:{rank}')
        if rank == args.rank:
            wandb.init(project='orig_sm_output_target_sm_gray', name=args.config, config=args.config_file)
            early_stopping = EarlyStopping(patience=5)

        train_dataset = CustomDataset(data_loader.train_valid_ind, data_loader.train_sm_data, data_loader.train_orig_sm_data, data_loader.train_mods_data, data_loader.skip_frames, args)
        test_dataset = CustomDataset(data_loader.test_valid_ind, data_loader.test_sm_data, data_loader.test_orig_sm_data, data_loader.test_mods_data, data_loader.skip_frames, args)
        print('Dataset is ready.')
        print('Train dataset length:', len(train_dataset))
        print('Test dataset length:', len(test_dataset))

        if world_size > 1:
            train_sampler = DistributedSampler(train_dataset, num_replicas=world_size, rank=rank, shuffle=True)
            test_sampler = DistributedSampler(test_dataset, num_replicas=world_size, rank=rank, shuffle=True)
        else:
            train_sampler = None
            test_sampler = None

        train_loader = DataLoader(
            train_dataset, 
            batch_size=args.bs, 
            num_workers=args.num_workers, 
            pin_memory=args.pin_memory,
            sampler=train_sampler,
            drop_last=True,
            shuffle=(train_sampler is None)
        )
        test_loader = DataLoader(
            test_dataset, 
            batch_size=args.bs,
            num_workers=args.num_workers,
            pin_memory=args.pin_memory, 
            sampler=test_sampler,
            drop_last=True,
            shuffle=(train_sampler is None)
        )
        print('DataLoader is ready.')

        ##################################################
        ############### Model Initialization ##############
        ##################################################
        if args.method == 'vae':
            from vae import VQVAE2
            model = VQVAE2(data_loader.channel_count).cuda(rank)
        elif args.method == 'simvp':
            from simvp import SimVP
            shape_in = (args.clip_len, data_loader.channel_count, args.img_sz, args.img_sz)
            model = SimVP(shape_in, args.pred_len, model_type=args.simvp_type, N_S=4, N_T=4).cuda(rank)
        elif args.method == 'vdt':
            # VAE setting
            from vae import VQVAE2
            vae_model = VQVAE2(data_loader.channel_count).cuda(rank)
            for param in vae_model.parameters():
                param.requires_grad = False # freeze vae
            if args.load_vae:
                vae_model = load_model(vae_model, args.vae_weight, rank)
            
            # VDT setting
            from vdt import VDT, VDT_L_2, VDT_S_2
            kwargs = {
                # vae latent shape -> (B, in_channels, input_size, input_size)
                'input_size': 16,
                'in_channels': 2,
                'num_frames': args.clip_len + args.pred_len,
            }
            if args.vdt_type == 'vdt_l':
                model = VDT_L_2(**kwargs).cuda(rank)
            elif args.vdt_type == 'vdt_s':
                model = VDT_S_2(**kwargs).cuda(rank)

            if rank == args.rank:
                from vdt import DDIM
                ddim = DDIM(model.iteration_num, model.beta_start, model.beta_end)
        
        print('Model is ready.')
        
        if world_size > 1:
            model = DDP(model, device_ids=[rank])

        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
        #scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=args.eta_min)
        
        #channel_sizes = [data_loader.valid_abc[mod][1] for mod in args.modality_list]
        channel_sizes = [data_loader.valid_abc[mod][1] for mod in ['soundmap']]
        for epoch in range(1, args.epochs+1):
            if world_size > 1:
                train_sampler.set_epoch(epoch)  # Shuffle for each epoch

            if rank == args.rank:
                pbar = tqdm(total=len(train_loader), desc=f'Epoch {epoch}/{args.epochs}', position=0, leave=True)

            # Potential additional metrics: FID, FVD, etc.
            epoch_train_mod_losses = {f'{mod}': [0, 0, 0] for mod in args.modality_list} # MSE, SSIM, PSNR
            epoch_test_mod_losses = {f'{mod}': [0, 0, 0] for mod in args.modality_list} # MSE, SSIM, PSNR
            
            # Train
            model.train()
            epoch_train_loss = 0
            epoch_train_peak_dist, epoch_train_peak_dist_by_time = 0, 0 # absolute distance (RSE) of peaks
            for _, (batch_data, batch_target) in enumerate(train_loader):
                batch_data = batch_data.cuda(rank).contiguous()
                
                # Data Augmentation
                if args.data_aug:
                    flip_flag, coords = FlipAndRandomCrop_step1(batch_data)
                    if args.method == 'vae':
                        batch_data = FlipAndRandomCrop_step2(batch_data, flip_flag, coords)
                    else:
                        batch_data = FlipAndRandomCrop_step2(batch_data, flip_flag, coords)
                        batch_target = FlipAndRandomCrop_step2(batch_target, flip_flag, coords)
                
                # Forward pass
                optimizer.zero_grad()
                if args.method == 'vae':
                    recon_x, latent_loss = model(batch_data.contiguous())
                    recon_loss = loss_func('mse', recon_x, batch_data)
                    if world_size > 1:
                        batch_train_loss = recon_loss + model.module.latent_loss_weight * latent_loss
                    else:
                        batch_train_loss = recon_loss + model.latent_loss_weight * latent_loss
                elif args.method == 'simvp':
                    batch_target = batch_target.cuda(rank).contiguous()
                    batch_output = model(batch_data.contiguous())
                    batch_train_loss = loss_func('mse', batch_output, batch_target)
                    
                elif args.method == 'vdt':
                    batch_target = batch_target.cuda(rank).contiguous()
                    if world_size > 1:
                        enc_vid, noise_target, t = model.module.preprocess_vdt_batch(vae_model, batch_data, batch_target)
                    else:
                        enc_vid, noise_target, t = model.preprocess_vdt_batch(vae_model, batch_data, batch_target)
                    noise_output = model(enc_vid, t.cuda(rank), args.pred_len)
                    batch_train_loss = loss_func('mse', noise_output, noise_target)

                batch_train_loss.backward()
                optimizer.step()
                epoch_train_loss += batch_train_loss.item()

                # Get MSE Train losses for each modality
                if rank == args.rank and args.method != 'vdt':
                    with torch.no_grad():
                        if args.method == 'vae':
                            per_channel_mse = MSE_Loss(recon_x, batch_data, reduction='none').double().mean(dim=(2, 3))
                            per_channel_ssim = ssim_per_sample_channel(recon_x, batch_data, window_size=11, sigma=1.5, data_range=1.0)
                            batch_peak_dist, batch_peak_dist_by_time = peak_dist(recon_x[:,0,:,:], batch_data[:,0,:,:])
                        else:
                            per_channel_mse = MSE_Loss(batch_output, batch_target, reduction='none').double().mean(dim=(1, 3, 4)) 
                            per_channel_ssim = ssim_per_sample_channel(batch_output, batch_target, window_size=11, sigma=1.5, data_range=1.0)
                            batch_peak_dist, batch_peak_dist_by_time = peak_dist(batch_output[:,:,0,:,:], batch_target[:,:,0,:,:])
                            
                        per_channel_psnr = 10 * torch.log10(1 / (per_channel_mse + 1e-8))
                        epoch_train_peak_dist += batch_peak_dist
                        epoch_train_peak_dist_by_time += batch_peak_dist_by_time
                        
                        mod_losses = {
                            "MSE": [mse.mean() for mse in torch.split(per_channel_mse, channel_sizes, dim=1)],
                            "SSIM": [ssim.mean() for ssim in torch.split(per_channel_ssim, channel_sizes, dim=1)],
                            "PSNR": [psnr.mean() for psnr in torch.split(per_channel_psnr, channel_sizes, dim=1)],
                        }
                        
                        for mod, mse, ssim, psnr in zip(args.modality_list, *mod_losses.values()):
                            epoch_train_mod_losses[mod] = [epoch_train_mod_losses[mod][i] + loss for i, loss in enumerate([mse, ssim, psnr])]

                if rank == args.rank:
                    pbar.update(1)
                    pbar.set_postfix({'Loss': f'{batch_train_loss.item():.4f}'})

            if rank == args.rank:
                pbar.close()
                wandb.log({'Epoch Loss/Train': epoch_train_loss/len(train_loader)}, step=epoch)
                if args.method != 'vdt':
                    wandb.log({'Epoch Peak Dist/Train': epoch_train_peak_dist / len(train_loader)}, step=epoch)    
                    if args.method != 'vae':
                        for t in range(args.pred_len):
                            wandb.log({f'Epoch Peak Dist/Train/t{t+1}': epoch_train_peak_dist_by_time[t] / len(train_loader)}, step=epoch)
                    for mod in args.modality_list:
                        for i, metric in enumerate(["MSE", "SSIM", "PSNR"]):
                            wandb.log({f'Epoch Mod {metric}/{mod}/Train': epoch_train_mod_losses[mod][i] / len(train_loader)}, step=epoch)

                plot_train_data = batch_data[:10].clone()
                plot_train_target = batch_target[:10].clone() if args.method != 'vae' else None

            # Evaluation
            model.eval()
            epoch_test_loss = 0
            epoch_test_peak_dist, epoch_test_peak_dist_by_time = 0, 0
            if rank == args.rank:
                pbar = tqdm(total=len(test_loader), desc='Testing', position=0, leave=True)

            with torch.no_grad():
                for _, (batch_data, batch_target) in enumerate(test_loader):
                    batch_data = batch_data.cuda(rank).contiguous()

                    if args.method == 'vae':
                        recon_x, latent_loss = model(batch_data)
                        recon_loss = loss_func('mse', recon_x, batch_data)
                        if world_size > 1:
                            batch_test_loss = recon_loss + model.module.latent_loss_weight * latent_loss
                        else:
                            batch_test_loss = recon_loss + model.latent_loss_weight * latent_loss
                    elif args.method == 'simvp':
                        batch_target = batch_target.cuda(rank).contiguous()
                        batch_output = model(batch_data)
                        batch_test_loss = loss_func('mse', batch_output, batch_target)
                    elif args.method == 'vdt':
                        batch_target = batch_target.cuda(rank).contiguous()
                        if world_size > 1:
                            enc_vid, noise_target, t = model.module.preprocess_vdt_batch(vae_model, batch_data, batch_target)
                        else:
                            enc_vid, noise_target, t = model.preprocess_vdt_batch(vae_model, batch_data, batch_target)
                        noise_output = model(enc_vid, t.cuda(rank), args.pred_len)
                        batch_test_loss = loss_func('mse', noise_output, noise_target)

                    # Get MSE Test losses for each modality
                    if rank == args.rank and args.method != 'vdt':
                        if args.method == 'vae':
                            per_channel_mse = MSE_Loss(recon_x, batch_data, reduction='none').double().mean(dim=(2, 3))
                            per_channel_ssim = ssim_per_sample_channel(recon_x, batch_data, window_size=11, sigma=1.5, data_range=1.0)
                            batch_peak_dist, batch_peak_dist_by_time = peak_dist(recon_x[:,0,:,:], batch_data[:,0,:,:])
                        else:
                            per_channel_mse = MSE_Loss(batch_output, batch_target, reduction='none').double().mean(dim=(1, 3, 4)) 
                            per_channel_ssim = ssim_per_sample_channel(batch_output, batch_target, window_size=11, sigma=1.5, data_range=1.0)
                            batch_peak_dist, batch_peak_dist_by_time = peak_dist(batch_output[:,:,0,:,:], batch_target[:,:,0,:,:])
                            
                        per_channel_psnr = 10 * torch.log10(1 / (per_channel_mse + 1e-8))
                        epoch_test_peak_dist += batch_peak_dist
                        epoch_test_peak_dist_by_time += batch_peak_dist_by_time
                        
                        mod_losses = {
                            "MSE": [mse.mean() for mse in torch.split(per_channel_mse, channel_sizes, dim=1)],
                            "SSIM": [ssim.mean() for ssim in torch.split(per_channel_ssim, channel_sizes, dim=1)],
                            "PSNR": [psnr.mean() for psnr in torch.split(per_channel_psnr, channel_sizes, dim=1)],
                        }
                        for mod, mse, ssim, psnr in zip(args.modality_list, *mod_losses.values()):
                            epoch_test_mod_losses[mod] = [epoch_test_mod_losses[mod][i] + loss for i, loss in enumerate([mse, ssim, psnr])]

                    epoch_test_loss += batch_test_loss.item()

                    if rank == args.rank:
                        pbar.update(1)
            
            if rank == args.rank:
                pbar.close()
                wandb.log({'Epoch Loss/Test': epoch_test_loss/len(test_loader)}, step=epoch)
                if args.method != 'vdt':
                    wandb.log({'Epoch Peak Dist/Test': epoch_test_peak_dist / len(test_loader)}, step=epoch)
                    if args.method != 'vae':
                        for t in range(args.pred_len):
                            wandb.log({f'Epoch Peak Dist/Test/t{t+1}': epoch_test_peak_dist_by_time[t] / len(test_loader)}, step=epoch)
                    for mod in args.modality_list:
                        for i, metric in enumerate(["MSE", "SSIM", "PSNR"]):
                            wandb.log({f'Epoch Mod {metric}/{mod}/Test': epoch_test_mod_losses[mod][i] / len(test_loader)}, step=epoch)

                plot_test_data = batch_data[:10].clone()
                plot_test_target = batch_target[:10].clone() if args.method != 'vae' else None

                print(f'Train Loss: {epoch_train_loss/len(train_loader):.10f}')
                print(f'Test Loss: {epoch_test_loss/len(test_loader):.10f}')

                # plot inference
                with torch.no_grad():
                    if args.method != 'vdt':
                        vae_model, ddim = None, None
                    plot_samples(args, world_size, epoch, plot_train_data, plot_train_target, model, vae_model=vae_model, ddim=ddim, train_or_test='train', save_or_show='save', channel_info=data_loader.valid_abc)
                    plot_samples(args, world_size, epoch, plot_test_data, plot_test_target, model, vae_model=vae_model, ddim=ddim, train_or_test='test', save_or_show='save', channel_info=data_loader.valid_abc)

                # early stopping
                early_stopping(epoch_test_loss / len(test_loader), model, args)
                early_stop_tensor = torch.tensor([1 if early_stopping.early_stop else 0], device=f'cuda:{rank}')
                if world_size > 1:
                    torch.distributed.broadcast(early_stop_tensor, src=rank)

            if early_stop_tensor.item() == 1:
                print("Early stopping")
                break

            #scheduler.step()
                
    
    except KeyboardInterrupt:
        print("Interrupted by user. Cleaning up...")
    finally:
        wandb.finish()
        cleanup()




if __name__ == '__main__':
    os.environ['CUDA_VISIBLE_DEVICES'] = '0,1,2,3' # 0,1,2,3
    os.environ['MASTER_ADDR'] = 'localhost'
    os.environ['MASTER_PORT'] = '6390' # 0~62235
    
    args = parse_args()

    data_loader = NPZLoader(args)
    print('NPZLoader is ready.')
    
    world_size = torch.cuda.device_count()
    #world_size = 1
    if world_size > 1:
        # Spawn multiple processes, one for each GPU
        mp.spawn(
            fn=train, 
            args=(world_size, args, data_loader), 
            nprocs=world_size, 
            join=True,
        )
    else:
        # Single GPU
        train(
            rank=args.rank, 
            world_size=world_size, 
            args=args,
            data_loader=data_loader,
        )


    

    



