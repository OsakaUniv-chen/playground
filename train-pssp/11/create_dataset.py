"""
workon beamforming
"""

import sys
sys.path.append('./soundmap')

import yaml
from sound_map import SoundMapGenerator
import soundfile as sf
import datetime
from scipy.signal import butter, lfilter

from audio_common_msgs.msg import AudioData, AudioDataStamped
from rosbags.rosbag2 import Reader as ROS2Reader
from rosbags.typesys import get_typestore, Stores
from rclpy.serialization import deserialize_message

import os
import gc
import io
import cv2
import subprocess
import numpy as np
from collections import deque
from pydub import AudioSegment

def butter_lowpass_filter(data, cutoff, fs, order=5):
    nyq = 0.5 * fs  # Nyquist frequency
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    return lfilter(b, a, data, axis=0)  # axis=0: time axis

def audiosegment_to_np(segment):
    samples = np.array(segment.get_array_of_samples(), dtype=np.int16)
    samples = samples.reshape((-1, 16)) 
    return samples

def reconstruct_audio(audio_segments, timestamps, target_rate=44100):
    audio_segments = [seg.apply_gain(30) for seg in audio_segments]
    timestamps = [ts / 1e9 for ts in timestamps]  # Convert to seconds
    start_time = timestamps[0] - (audio_segments[0].duration_seconds)  # Actual start time
    end_time = timestamps[-1]
    total_duration = end_time - start_time
    total_samples = int(total_duration * target_rate)
    print(f"Start: {start_time}, End: {end_time}, Duration: {total_duration}s, Samples: {total_samples}")

    audio_output = np.zeros((total_samples, 16), dtype=np.int16)
    last_end_sample = -1
    for segment, ts in zip(audio_segments, timestamps):
        np_chunk = audiosegment_to_np(segment)  # shape: (N, 16)
        segment_duration, segment_samples = segment.duration_seconds, len(np_chunk)
        segment_start_time = ts - segment_duration  # Correct start time
        start_sample = max(last_end_sample, max(0, int((segment_start_time - start_time) * target_rate)))
        if start_sample + segment_samples > total_samples:
            np_chunk = np_chunk[:total_samples - start_sample]
        audio_output[start_sample:start_sample + len(np_chunk)] = np_chunk
        last_end_sample = start_sample + len(np_chunk) - 1

    audio_output = butter_lowpass_filter(audio_output, cutoff=4000, fs=44100)
    audio_output = np.clip(audio_output, -32768, 32767).astype(np.int16)

    return audio_output

def cvt_timestamp(timestamp):
    seconds = timestamp // 1e9
    date_time = datetime.datetime.fromtimestamp(seconds)
    str_date_time = date_time.strftime('%Y-%m-%d_%H:%M:%S:')
    return date_time, str_date_time

###################################################
###################################################
###################################################

dataset_debate = ['debate_exp1_topic1', 'debate_exp1_topic2', 'debate_exp1_topic3']
dataset_demo = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']
dataset_becap = ['0318_demo_a1', '0318_demo_a2', '0318_demo_aa1', '0318_demo_aa2', '0318_demo_b1', '0318_demo_b2']
dataset_riken3f = ['2025-04-15-15_49_10']
dataset_olab_rev_0630 = ['2025-06-30-13_13_56', '2025-06-30-14_08_55', '2025-06-30-14_34_27', '2025-06-30-14_52_51', '2025-06-30-15_17_14', '2025-06-30-15_27_52', '2025-06-30-15_34_05', '2025-06-30-15_48_08', '2025-06-30-15_58_04', '2025-06-30-16_08_32', '2025-06-30-16_20_39', '2025-06-30-16_32_04', '2025-06-30-16_41_20']
dataset_olab_0630 = ['2025-06-30-14_10_14', '2025-06-30-14_34_55', '2025-06-30-14_52_39', '2025-06-30-15_17_22', '2025-06-30-15_27_42', '2025-06-30-15_33_57', '2025-06-30-15_47_37', '2025-06-30-15_57_54', '2025-06-30-16_08_22', '2025-06-30-16_21_08', '2025-06-30-16_26_02', '2025-06-30-16_31_06', '2025-06-30-16_40_52']
dataset_mobile_3f = ['2024-10-23-17_25_43', '2024-10-24-16_45_36', '2024-10-24-16_54_21', '2024-10-30-18_20_48']
dataset_grp_mtg = ['2025-07-17-13_06_49', '2025-07-17-15_16_21', '2025-07-24-15_06_03']
dataset_egosas = ['2025-09-16-16_45_27', '2025-09-16-16_25_26', '2025-09-16-16_55_27', '2025-09-16-16_10_18', '2025-09-16-16_35_27', '2025-09-16-11_17_51', '2025-09-16-16_56_59', '2025-09-16-16_15_26', '2025-09-16-10_46_05', '2025-09-16-15_29_44']
dataset_experiment1126 = ['EXP1_Video', 'EXP1_Tele', 'EXP1_PSSP', 'EXP1_DoA', 'EXP1_Random', 'EXP2_Video', 'EXP2_Tele', 'EXP2_PSSP', 'EXP2_DoA', 'EXP2_Random']
dataset_expo2025 = ['2025-09-28-13_12_06', '2025-09-28-13_22_12', '2025-09-28-13_32_27', '2025-09-28-13_42_33', '2025-09-28-13_52_39', '2025-09-28-14_35_42', '2025-09-28-14_45_48', '2025-09-28-14_55_54', '2025-09-28-15_06_01', '2025-09-28-15_16_07', '2025-09-28-15_26_13', '2025-09-28-16_00_35', '2025-09-28-16_10_41', '2025-09-28-16_20_47', '2025-09-28-16_30_53', '2025-09-28-16_41_00', '2025-09-28-16_51_06', '2025-09-28-17_01_12', '2025-09-28-17_28_55', '2025-09-29-11_03_50', '2025-09-29-11_13_56', '2025-09-29-11_24_02', '2025-09-29-11_34_09', '2025-09-29-11_44_15', '2025-09-29-11_54_21', '2025-09-29-13_00_18', '2025-09-29-13_10_33', '2025-09-29-13_20_48', '2025-09-29-13_31_04', '2025-09-29-13_41_19', '2025-09-29-13_51_34']
dataset_experiment0312 = ['EXP3_Video', 'EXP3_Tele', 'EXP3_PSSP', 'EXP3_DoA', 'EXP3_Random']
#['2025-09-16-16_10_18', '2025-09-16-16_15_26', '2025-09-16-16_25_26', '2025-09-16-16_35_27', '2025-09-16-16_45_27', '2025-09-16-16_55_27', '2025-09-16-16_56_59', '2025-09-17-15_30_24', '2025-09-17-15_40_30', '2025-09-17-15_49_09', '2025-09-17-15_59_24', '2025-09-17-16_11_21', '2025-09-17-16_21_27', '2025-09-17-16_31_32', '2025-09-17-17_23_22', '2025-09-17-17_33_28', '2025-09-17-17_43_33', '2025-09-17-17_53_39', '2025-09-17-18_02_16', '2025-09-17-18_12_22', '2025-09-20-11_11_04', '2025-09-20-11_21_10', '2025-09-20-11_31_16', '2025-09-20-11_41_22', '2025-09-20-11_51_29', '2025-09-20-13_07_21', '2025-09-20-13_17_27', '2025-09-20-13_27_33', '2025-09-20-13_37_39', '2025-09-20-13_47_45', '2025-09-20-13_57_52', '2025-09-20-14_30_36', '2025-09-20-14_40_42', '2025-09-20-14_50_48', '2025-09-20-15_00_54', '2025-09-20-15_11_00', '2025-09-20-15_21_06', '2025-09-23-11_21_43', '2025-09-23-11_01_32', '2025-09-23-11_31_49', '2025-09-23-11_11_37', '2025-09-23-11_41_55', '2025-09-23-11_52_01', '2025-09-23-12_02_07', '2025-09-23-13_05_12', '2025-09-23-13_15_18', '2025-09-23-13_25_24', '2025-09-23-13_35_30', '2025-09-23-13_45_36', '2025-09-23-13_55_43', '2025-09-23-14_05_49', '2025-09-24-10_59_05', '2025-09-24-11_09_11', '2025-09-24-11_19_17', '2025-09-24-11_29_23', '2025-09-24-11_39_30', '2025-09-24-11_49_36', '2025-09-24-11_59_42', '2025-09-24-12_58_40', '2025-09-24-13_08_47', '2025-09-24-13_18_53', '2025-09-24-13_28_59', '2025-09-24-13_39_05', '2025-09-24-13_49_11', '2025-09-24-13_59_17', '2025-09-25-11_00_42', '2025-09-25-11_10_49', '2025-09-25-11_20_55', '2025-09-25-11_31_01', '2025-09-25-11_41_07', '2025-09-25-11_51_14', '2025-09-25-12_01_19', '2025-09-25-12_59_26', '2025-09-25-13_09_32', '2025-09-25-13_19_39', '2025-09-25-13_29_45', '2025-09-25-13_40_00', '2025-09-25-14_28_30', '2025-09-25-14_38_35', '2025-09-25-14_48_42', '2025-09-25-14_58_48', '2025-09-25-15_08_54', '2025-09-25-15_19_00', '2025-09-25-15_59_03', '2025-09-25-16_09_10', '2025-09-25-16_19_16', '2025-09-25-16_29_22', '2025-09-25-16_39_29', '2025-09-25-16_49_35', '2025-09-25-17_29_56', '2025-09-25-17_40_02', '2025-09-25-17_50_09', '2025-09-25-18_00_15', '2025-09-25-18_10_21', '2025-09-25-18_20_27', '2025-09-27-10_59_12', '2025-09-27-11_09_18', '2025-09-27-11_19_24', '2025-09-27-11_29_30', '2025-09-27-11_39_37', '2025-09-27-11_49_43', '2025-09-27-11_59_49', '2025-09-27-12_59_03', '2025-09-27-13_09_09', '2025-09-27-13_19_16', '2025-09-27-13_29_31', '2025-09-27-13_39_37', '2025-09-27-13_49_42', '2025-09-27-13_59_48', '2025-09-27-14_29_56', '2025-09-27-14_40_02', '2025-09-27-14_50_09', '2025-09-27-15_00_15', '2025-09-27-15_10_21', '2025-09-27-15_20_27', '2025-09-27-15_59_18', '2025-09-27-16_09_24', '2025-09-27-16_19_30', '2025-09-27-16_29_35', '2025-09-27-16_39_42', '2025-09-27-16_49_48', '2025-09-27-17_29_03', '2025-09-27-17_39_09', '2025-09-27-17_49_15', '2025-09-27-17_59_21', '2025-09-27-18_09_27', '2025-09-27-18_19_33', '2025-09-28-10_59_56', '2025-09-28-11_10_02', '2025-09-28-11_20_08', '2025-09-28-11_30_15', '2025-09-28-11_40_21', '2025-09-28-11_50_27', '2025-09-28-12_00_33', '2025-09-28-13_02_00', ]

dataset = dataset_experiment0312

data_folder = '/media/chen/Extreme SSD/Data/RawData'
#data_folder = '../original_data'
recv_stamp = False
for bag_name in dataset:
    if bag_name in dataset_debate:
        bag_path = f'{data_folder}/chat/{bag_name}'
    elif bag_name in dataset_demo:
        bag_path = f'{data_folder}/Demonstration_Data/{bag_name}'
    elif bag_name in dataset_becap:
        bag_path = f'{data_folder}/demo_data_0318_becap/{bag_name}'
    elif bag_name in dataset_riken3f:
        bag_path = f'{data_folder}/riken_3f/{bag_name}'
    elif bag_name in dataset_olab_rev_0630:
        bag_path = f'{data_folder}/olab_rev_0630/{bag_name}/'
    elif bag_name in dataset_olab_0630:
        bag_path = f'{data_folder}/olab_0630/{bag_name}/'
    elif bag_name in dataset_mobile_3f:
        bag_path = f'{data_folder}/ProjectMobileRobot_3f/{bag_name}/'
    elif bag_name in dataset_grp_mtg:
        bag_path = f'{data_folder}/GRP_meeting/{bag_name}/'
        recv_stamp = True
    elif bag_name in dataset_egosas:
        bag_path = f'{data_folder}/egoSAS_test_data/{bag_name}/'
        recv_stamp = True
    elif bag_name in dataset_experiment1126:
        bag_path = f'{data_folder}/Experiment1126/{bag_name}/'
        recv_stamp = True
    elif bag_name in dataset_expo2025:
        bag_path = f'{data_folder}/expo_2025/{bag_name}/'
        recv_stamp = True
    elif bag_name in dataset_experiment0312:
        bag_path = f'{data_folder}/Experiment0312/{bag_name}/'
        recv_stamp = True
    

    ###################################################
    ###################################################
    ###################################################
    xml_path = './soundmap/acoular/xml/minidsp_uma-16.xml'

    fs = 44100
    channels = 16
    blocksize = 4096 # 128/256/512/1024/2048/4096/8192/16384/32768/65536
    sm_size = 64 # 64
    plot_size = 1080
    
    image_topic = '/camera/image_raw/compressed'
    typestore = get_typestore(Stores.ROS2_HUMBLE)
    audio_topic = '/audio/audio_raw'
    audio_max_len = 160 # 160*128/44100 = 0.46s
    sm_generator = SoundMapGenerator(fs=fs, channels=channels, blocksize=blocksize, xml_path=xml_path, sm_size=sm_size, plot_size=plot_size)
    
    initialize_flag = True
    bag_count = 1

    with open(f"{bag_path}/metadata.yaml", "r") as f:
        data = yaml.safe_load(f)
    total_msg = data["rosbag2_bagfile_information"]["message_count"]
    print(f'Processing {bag_name}, total messages: {total_msg}')

    with ROS2Reader(bag_path) as ros2_reader:
        ros2_conns = [x for x in ros2_reader.connections]
        print([x.topic for x in ros2_conns])

        ros2_messages = ros2_reader.messages(connections=ros2_conns)
        for m, msg in enumerate(ros2_messages):
            (connection, timestamp, rawdata) = msg

            # Initialize everything as a new bag
            if initialize_flag:
                video_img = []
                video_ts = []
                audio_segment = []
                audio_ts = []
                audio_queue = deque(maxlen=audio_max_len)
                audio_queue_oneframe = deque()
                audio_bytes = bytearray()
                sync_flag = [False, False] # [Video, Audio]
                sm_array = []
                cam_array = []
                audio_array = []
                initialize_flag = False
            

            if (connection.topic == audio_topic) and all(sync_flag):
                if recv_stamp:
                    deData = deserialize_message(rawdata, AudioDataStamped)
                else:
                    deData = deserialize_message(rawdata, AudioData)
                deBytes = deData.data.tobytes()
                audio_bytes.extend(deBytes)

                one_segment = AudioSegment.from_raw(io.BytesIO(deBytes), frame_rate=fs, channels=channels, sample_width=2)
                audio_queue.append(one_segment)
                audio_queue_oneframe.append(one_segment)
                
            if (connection.topic == image_topic) and all(sync_flag) and len(audio_queue) > 0:
                t1 = cv2.getTickCount()

                data = typestore.deserialize_cdr(rawdata, connection.msgtype)
                camera_img = cv2.imdecode(np.frombuffer(data.data, np.uint8), cv2.IMREAD_COLOR)
                if bag_name in dataset_olab_rev_0630:
                    camera_img = cv2.flip(camera_img, -1)
                camera_img_orig = camera_img.copy()
                camera_img_orig = cv2.resize(camera_img_orig, (1080, 1080), interpolation=cv2.INTER_LINEAR)
                camera_img = cv2.resize(camera_img, (sm_size, sm_size))
                gray_img = cv2.cvtColor(camera_img, cv2.COLOR_BGR2GRAY)
                blend_img = cv2.resize(camera_img_orig, (plot_size, plot_size))#camera_img.copy()

                # Audio
                audio_segment.append(AudioSegment.from_raw(io.BytesIO(audio_bytes), frame_rate=fs, channels=channels, sample_width=2))
                audio_ts.append(timestamp)
                audio_bytes.clear()

                sm = sm_generator.generate(audio_queue)
                filled_sm, clipped_filled_sm = sm_generator.visualize_sm(sm, method='B')
                
                if len(audio_queue_oneframe) == 0:
                    save_audio_queue = np.zeros((channels, 0), dtype=np.int16)
                    print('Audio queue is empty, saving empty audio array.')
                else:
                    save_audio_queue = sum(audio_queue_oneframe).apply_gain(30)
                    save_audio_queue = np.frombuffer(save_audio_queue.raw_data, dtype=np.int16).reshape(channels, -1)

                #sm = np.exp(sm-sm.max()).astype(np.float64)
                sm_array.append(sm)
                cam_array.append(gray_img)
                audio_array.append(save_audio_queue)
                audio_queue_oneframe = deque()
                
                print(camera_img_orig.shape, sm.shape, gray_img.shape, save_audio_queue.shape)
                #input('Press Enter to continue...')
                
                for point in sm_generator.points1:
                    cv2.circle(blend_img, point, 2, (255, 0, 0), -1)
                for point in sm_generator.points2:
                    cv2.circle(blend_img, point, 2, (0, 255, 0), -1)
                for point in sm_generator.points3:
                    cv2.circle(blend_img, point, 2, (0, 0, 255), -1)
                
                blend_img = cv2.addWeighted(clipped_filled_sm, 0.6, blend_img, 0.8, 0)
                blend_img = np.hstack((blend_img, filled_sm))
                blend_img = cv2.resize(blend_img, (1280,640), interpolation=cv2.INTER_LINEAR)
                cv2.imshow('blend', blend_img)
                print(timestamp)
                cv2.waitKey(1)

                blend_img = cv2.resize(blend_img, (640,320), interpolation=cv2.INTER_LINEAR)
                video_img.append(blend_img)
                video_ts.append(timestamp)

                t2 = cv2.getTickCount()
                print(f'{bag_name} Time: {(t2 - t1) / cv2.getTickFrequency():.2f}')

            if (connection.topic == image_topic):
                sync_flag[0] = True
            elif (connection.topic == audio_topic):
                sync_flag[1] = True

            if m % 100 == 0:
                gc.collect()


            if len(sm_array) > 30000 or (m == total_msg - 1):
                audio_array_obj = np.empty(len(audio_array), dtype=object)
                for i, a in enumerate(audio_array):
                    audio_array_obj[i] = a

                cv2.destroyAllWindows()
                np.savez_compressed(f'/media/chen/Extreme SSD/Data/ProcessedData/{bag_name if bag_count==1 else f"{bag_name}_{bag_count}"}.npz', soundmap=sm_array, gray=cam_array, audio=audio_array_obj)
                del sm_array, cam_array, audio_array, audio_array_obj
                del audio_bytes, audio_queue_oneframe, audio_queue, sync_flag
                gc.collect()
                print('npz saved.')

                # Make Video
                sf.write("temp_audio.wav", reconstruct_audio(audio_segment, audio_ts), 44100)
                del audio_segment, audio_ts
                gc.collect()

                height, width, _ = video_img[0].shape
                avg_time_interval = (video_ts[-1] - video_ts[0]) / (len(video_ts) - 1)
                fps = 1 / (avg_time_interval / 1e9)
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                video_writer = cv2.VideoWriter('temp_video.mp4', fourcc, fps, (width, height))
                for i in range(len(video_img)):
                    video_writer.write(video_img[i])
                video_duration_sec = len(video_img) / fps
                video_writer.release()

                start_ts, str_start_ts = cvt_timestamp(video_ts[0])
                end_ts, str_end_ts = cvt_timestamp(video_ts[-1])
                media_total_sec = int((end_ts - start_ts).total_seconds())
                media_min = media_total_sec // 60
                media_sec = media_total_sec % 60
                print(fps, start_ts, end_ts, len(video_ts), len(video_img))
                del video_img, video_ts
                gc.collect()

                result = bag_path+'/{}_SM_{}_min_{}_sec.mp4'.format(bag_name if bag_count==1 else f"{bag_name}_{bag_count}", media_min, media_sec)
                if bag_name in dataset_expo2025:
                    result = '/media/chen/Extreme SSD/Data/RawData/expo_2025/Videos/{}_SM_{}_min_{}_sec.mp4'.format(bag_name if bag_count==1 else f"{bag_name}_{bag_count}", media_min, media_sec)
                subprocess.run(['ffmpeg', '-y', '-i', 'temp_video.mp4', '-i', 'temp_audio.wav', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '320k', '-ac', '2', result])
                
                subprocess.run(['rm', 'temp_audio.wav'])
                subprocess.run(['rm', 'temp_video.mp4'])

                print(f"Video Duration: {video_duration_sec:.2f} seconds")

                initialize_flag = True
                bag_count += 1




    


    
