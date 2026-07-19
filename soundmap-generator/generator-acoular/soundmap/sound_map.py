import acoular
acoular.config.global_caching = 'none'

import cv2
import torch
import numpy as np
from scipy.interpolate import griddata
    

class SoundMapGenerator:
    def __init__(self, fs, channels, blocksize, xml_path, sm_size, plot_size=1080):
        self.fs = fs
        self.channels = channels
        self.blocksize = blocksize
        self.sm_size = sm_size
        self.plot_size = plot_size
        
        self.synthetic_f = 2000
        self.synthetic_num = 3
        
        self.bf_method = 'Base' # 'Base', 'Eig', 'Capon' -> These three are good
        if self.bf_method in ['Base', 'Eig']:
            self.Lm_th, self.Lm_base = 1, 90#0.1, 125 # 1, 118
        elif self.bf_method == 'Functional':
            self.Lm_th, self.Lm_base = 1, 110
            self.gamma = 50
        elif self.bf_method == 'Cleansc':
            self.Lm_th, self.Lm_base = 0.1, 120
        elif self.bf_method == 'Capon': # Good
            self.Lm_th, self.Lm_base = 0.3, 56
        
        self.distance = 1.5
        sound_speed = 345 # Default Sound Speed: 343.0 m/s
        self.mg = acoular.MicGeom(from_file=xml_path)
        self.env = acoular.Environment(c=sound_speed)
        
        self.points = None
        self.grid_x, self.grid_y = np.mgrid[0:1080, 0:1080]
        
    def generate_oneLm(self, rg, ps):
        st = acoular.SteeringVector(grid=rg, mics=self.mg, env=self.env)

        if self.bf_method == 'Base':
            bb = acoular.BeamformerBase(freq_data=ps, steer=st, r_diag=True)
        elif self.bf_method == 'Eig':
            bb = acoular.BeamformerEig(freq_data=ps, steer=st, r_diag=True)
        elif self.bf_method == 'Functional':
            bb = acoular.BeamformerFunctional(freq_data=ps, steer=st, gamma=self.gamma)
        elif self.bf_method == 'Cleansc':
            bb = acoular.BeamformerCleansc(freq_data=ps, steer=st, r_diag=True)
        elif self.bf_method == 'Capon':
            bb = acoular.BeamformerCapon(freq_data=ps, steer=st)
        
        pm = bb.synthetic(f=self.synthetic_f, num=self.synthetic_num)
        Lm = acoular.L_p(pm)#.T
        #Lm = cv2.flip(Lm, 0) # The sound map

        return Lm
    
    def create_uv(self, rg, distance):
        pixel_size = 1080
        r_max = np.pi / 2
        X = rg.gpos[0, :]
        Y = rg.gpos[1, :]
    
        r = np.arctan(np.sqrt(X**2 + Y**2) / distance)
        r_normalized = (r / r_max) * (pixel_size / 2)
        theta = np.arctan2(Y, X)

        u = (pixel_size / 2 + r_normalized * np.cos(theta)).astype(int)
        v = (pixel_size / 2 + r_normalized * np.sin(theta)).astype(int)
        
        return u, v

    
    def visualize_sm(self, final_Lm, method='B'):
        plot_sm = cv2.resize(final_Lm, (self.plot_size, self.plot_size), interpolation=cv2.INTER_LINEAR)

        def transform(x, method):
            if isinstance(x, np.ndarray):
                x = x.astype(np.float64)
            elif isinstance(x, torch.Tensor):
                x = x.to(torch.float64)

            if method == 'A': # raw
                return x/160
            elif method == 'B': # np.exp(x-x.max())
                return np.exp(x-x.max())
            elif method == 'C': # np.exp((x-x.max())/(x.max()-x.min()))
                if x.max() == x.min():
                    return np.zeros_like(x)
                return np.exp((x-x.max())/(x.max()-x.min()))
            elif method == 'D': # np.exp(x)
                x = np.exp(x)
                return (x - x.min()) / (x.max() - x.min())
            elif method == 'E': # np.exp(x)
                x = np.exp(x)
                return (x - 1) / (np.exp(160) - 1)
        
        # visualization of filled_sm
        filled_sm = transform(plot_sm, method)
        filled_sm = (filled_sm * 255).astype(np.uint8)
        if method == 'A':
            print(final_Lm.min(), final_Lm.max(), filled_sm.min(), filled_sm.max())
        filled_sm = np.stack([np.zeros_like(filled_sm), filled_sm, filled_sm], axis=-1)
        
        return filled_sm
    
    
    def generate(self, audio_queue):
        audio_queue = [np.frombuffer(a, dtype=np.int16).reshape(-1, self.channels) for a in audio_queue]
        
        audio_np_array = np.vstack(audio_queue)
        gain_factor = 10**(30/20)  # Convert dB to amplitude factor
        audio_np_array = (audio_np_array * gain_factor).astype(np.int16)

        time_samples = acoular.TimeSamples(numchannels=self.channels, numsamples=len(audio_np_array), sample_freq=self.fs)
        time_samples.data = audio_np_array
        ps = acoular.PowerSpectra(time_data=time_samples, block_size=self.blocksize, window='Blackman-Harris', overlap='66.1%')

        fine_grid = False
        if fine_grid:
            # use this to ensure 2 Hz output
            rg1 = acoular.RectGrid(x_min=-5.2, x_max=5.2, y_min=-5.2, y_max=5.2, increment=0.4, z=1.5)
            rg2 = acoular.RectGrid(x_min=-2.5, x_max=2.5, y_min=-2.5, y_max=2.5, increment=0.2, z=1.5)
            rg3 = acoular.RectGrid(x_min=-1.25, x_max=1.25, y_min=-1.25, y_max=1.25, increment=0.25, z=1.5)
        else:
            # use this to ensure 4 Hz output
            rg1 = acoular.RectGrid(x_min=-5.0, x_max=5.0, y_min=-5.0, y_max=5.0, increment=1, z=1.5)
            rg2 = acoular.RectGrid(x_min=-2.5, x_max=2.5, y_min=-2.5, y_max=2.5, increment=0.5, z=1.5)
            rg3 = acoular.RectGrid(x_min=-1.25, x_max=1.25, y_min=-1.25, y_max=1.25, increment=0.1, z=1.5)

        mg = acoular.MergeGrid(grids=[rg1, rg2, rg3])
        Lm = self.generate_oneLm(mg, ps)

        if self.points is None:
            v, u = self.create_uv(mg, self.distance)
           
            # transform
            v = 1080 - v
            cx, cy = 540, 540
            u = 2*cx - u
            v = 2*cy - v

            self.points = np.array([u, v]).T
        
        # Interpolate
        transformed_values = np.clip(Lm, 0, None) # if -350 exists then converts it to 0
        interpolated_Lm = griddata(self.points, transformed_values, (self.grid_x, self.grid_y), method='linear', fill_value=0)
        final_Lm = cv2.resize(interpolated_Lm, (self.sm_size, self.sm_size), interpolation=cv2.INTER_LINEAR)
        final_Lm = np.clip(final_Lm, 0, 160) # griddata may produce <0 values, and limits the max value to 160

        return final_Lm


    
    

