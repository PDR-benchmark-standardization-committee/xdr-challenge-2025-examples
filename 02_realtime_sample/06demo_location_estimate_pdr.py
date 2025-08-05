#! /usr/bin/env -S python3 -O
#! /usr/bin/env -S python3

import os
import sys
import lzma
import requests
import time
from parse import parse
import yaml
import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation

from enum import Enum

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import matplotlib.animation as animation
from collections import deque

from evaalapi import statefmt, estfmt

server = "http://127.0.0.1:5000/evaalapi/"
trialname = "onlinedemo"


def spherical_to_cartesian(distance, azimuth_deg, elevation_deg):
    azimuth_rad = np.radians(azimuth_deg)
    elevation_rad = np.radians(elevation_deg)
    
    x = distance * np.cos(elevation_rad) * np.sin(azimuth_rad)
    y = distance * np.cos(elevation_rad) * np.cos(azimuth_rad)
    z = distance * np.sin(elevation_rad)
    
    return x, y, z


def recover_quat(quat_xyz):
    norm_xyz = (quat_xyz[0]**2 + quat_xyz[1]**2 + quat_xyz[2]**2)
    if norm_xyz > 1.0: 
        norm_xyz = 1.0
        quat_w = 0
    else:
        quat_w = np.sqrt(1 - (quat_xyz[0]**2 + quat_xyz[1]**2 + quat_xyz[2]**2))
    q = np.array([quat_xyz[0], quat_xyz[1], quat_xyz[2], quat_w])
    
    return q


def get_yaw_from_quat(quat_xyzw):
    x, y, z, w = quat_xyzw
    yaw = np.arctan2(2.0 * (w*z + x*y), 1.0 - 2.0 * (y*y + z*z))
    return yaw


def wrap_angle_pi(angle):
    """Wrap angle to (-π, π] range"""
    wrapped = angle % (2 * np.pi)
    if wrapped > np.pi:
        wrapped -= 2 * np.pi
    return wrapped


class SimplePDR:
    def __init__(self, acc_thresh=0.1, pdr_window_sec=1.0, default_velocity=0.7):
        self.acc_thresh = acc_thresh
        self.pdr_window_sec = pdr_window_sec
        self.default_velocity = default_velocity
    
    def estimate(self, df_acc):
        # we just threshold the acceleration to estimate walking / non-walking state
        # we assume that the subject is walking at a constant speed for simplicity
        df_acc["total"] = np.sqrt(df_acc["x"]**2 + df_acc["y"]**2 + df_acc["z"]**2) - 1.0

        # Calculate RMS of total acceleration using rolling window
        a_squared = df_acc["total"] ** 2
        a_rms = np.sqrt(a_squared.rolling(window=f"{self.pdr_window_sec}s", min_periods=1, center=True).mean())
        
        # Apply threshold to detect walking
        velocities = np.zeros_like(a_rms)
        velocities[a_rms > self.acc_thresh] = self.default_velocity
        
        # Return the latest velocity value
        velocity = velocities[-1]
        
        return velocity, a_rms


class LocStatus(Enum):
    INITIALIZING = 0
    INITIALIZED = 1

class DemoLocalizer:
    
    def __init__(self, pdr_model, df_convert_window=20):
        self.acce_data = []
        self.gyro_data = []
        self.magn_data = []
        self.ahrs_data = []
        self.uwbp_data = []
        self.uwbt_data = []
        self.gpos_data = []
        self.viso_data = []
        self.last_est = (0, 0, 0)
        self.pdr_model = pdr_model
        self.df_convert_window = df_convert_window
        
        self.pdr_estimates = []
        self.yaw_angles = []
        self.vio_estimates = []
        self.last_vio_pose = None
        
        self.state = LocStatus.INITIALIZING
        self.last_estimate_ts = None
        
        self.newest_data_ts = 0
        
    def __str__(self):
        str_data = "Stored data \n"
        str_data +=  f"acce: {self.acce_data} \n"
        str_data +=  f"gyro: {self.gyro_data} \n"
        str_data +=  f"magn: {self.magn_data} \n"
        str_data +=  f"ahrs: {self.ahrs_data} \n"
        str_data +=  f"uwbp: {self.uwbp_data} \n"
        str_data +=  f"uwbt: {self.uwbt_data} \n"
        str_data +=  f"gpos: {self.gpos_data} \n"
        str_data +=  f"viso: {self.viso_data} \n"
        return str_data

    def update_timestamp(self, data):
        if data["sensor_timestamp"] > self.newest_data_ts:
            self.newest_data_ts = data["sensor_timestamp"]
    
    def callback_acce(self, data):
        self.acce_data.append(data)
        self.update_timestamp(data)
        
        if len(self.acce_data) > self.df_convert_window:
            acce_data_to_process = self.acce_data[-self.df_convert_window:-1]
        else:
            acce_data_to_process = self.acce_data
        df_acc = pd.DataFrame(acce_data_to_process)
        df_acc = df_acc.rename(columns={"sensor_timestamp": "timestamp", "acc_x": "x", "acc_y": "y", "acc_z": "z"}).set_index("timestamp")
        df_acc.index = pd.to_datetime(df_acc.index, unit="s")
        velocity, a_rms = self.pdr_model.estimate(df_acc)
        
        ts = df_acc.index[-1]
        self.pdr_estimates.append({"timestamp": ts, "velocity": velocity})

    def callback_gyro(self, data):
        self.gyro_data.append(data)        
        self.update_timestamp(data)
        
    def callback_magn(self, data):
        self.magn_data.append(data)
        self.update_timestamp(data)
        
    def callback_ahrs(self, data):
        self.ahrs_data.append(data)
        self.update_timestamp(data)
        
        ts = data["sensor_timestamp"]
        
        yaw = (data["yaw_z"])/180 * np.pi
        
        dyaw = 0
        if len(self.yaw_angles) > 1:
            dyaw = wrap_angle_pi(yaw - self.yaw_angles[-1]["yaw"])
        
        self.yaw_angles.append({"timestamp" : ts, "yaw": yaw, "dyaw" : dyaw})
        
    def callback_uwbp(self, data):
        self.uwbp_data.append(data)
        self.update_timestamp(data)
        
    def callback_uwbt(self, data):
        self.uwbt_data.append(data)
        self.update_timestamp(data)
        
    def callback_gpos(self, data):
        self.gpos_data.append(data)
        self.update_timestamp(data)

        if self.state == LocStatus.INITIALIZING and data["object_id"] == "base_link":
            q = recover_quat([data["quat_x"], data["quat_y"], data["quat_z"]])
            yaw = get_yaw_from_quat(q)
            self.last_est = (data["location_x"], data["location_y"], yaw)
            self.last_estimate_ts = data["sensor_timestamp"]
            self.state = LocStatus.INITIALIZED
        
    def callback_viso(self, data):
        self.viso_data.append(data)
        self.update_timestamp(data)
        
        ts = data["sensor_timestamp"]
        
        current_pose = {
            "timestamp": ts,
            "x": data["location_x"],
            "y": data["location_y"], 
            "z": data["location_z"],
            "quat_x": data["quat_x"],
            "quat_y": data["quat_y"],
            "quat_z": data["quat_z"]
        }
        
        if self.last_vio_pose is not None:
            dx = current_pose["x"] - self.last_vio_pose["x"]
            dy = current_pose["y"] - self.last_vio_pose["y"]
            dz = current_pose["z"] - self.last_vio_pose["z"]
            
            curr_q = recover_quat([current_pose["quat_x"], current_pose["quat_y"], current_pose["quat_z"]])
            last_q = recover_quat([self.last_vio_pose["quat_x"], self.last_vio_pose["quat_y"], self.last_vio_pose["quat_z"]])
            
            curr_yaw = get_yaw_from_quat(curr_q)
            last_yaw = get_yaw_from_quat(last_q)
            dyaw = wrap_angle_pi(curr_yaw - last_yaw)
            
            dt = ts - self.last_vio_pose["timestamp"]
            
            self.vio_estimates.append({
                "timestamp": ts,
                "dx": dx,
                "dy": dy,
                "dz": dz,
                "dyaw": dyaw,
                "dt": dt
            })
        
        self.last_vio_pose = current_pose
        
    def callback(self, sensor_type, data):
        if sensor_type == "ACCE":
            self.callback_acce(data)
        if sensor_type == "GYRO":
            self.callback_gyro(data)
        if sensor_type == "MAGN":
            self.callback_magn(data)
        if sensor_type == "AHRS":
            self.callback_ahrs(data)
        if sensor_type == "UWBP":
            self.callback_uwbp(data)
        if sensor_type == "UWBT":
            self.callback_uwbt(data)
        if sensor_type == "GPOS":
            self.callback_gpos(data)
        if sensor_type == "VISO":
            self.callback_viso(data)

    def get_latest_tag_pose(self, tag_id):
        latest_gpos = None
        for d in reversed(self.gpos_data):
            if d["object_id"] == tag_id:
                latest_gpos = d
                break
            
        loc = None
        q = None
        if latest_gpos is not None:            
            loc = np.array((latest_gpos["location_x"], latest_gpos["location_y"], latest_gpos["location_z"]))
            q = recover_quat([latest_gpos["quat_x"], latest_gpos["quat_y"], latest_gpos["quat_z"]])
        return loc, q

    def predict_by_pdr(self):
        if self.last_estimate_ts is None or len(self.pdr_estimates) == 0 or len(self.yaw_angles) == 0:
            return self.last_est
        
        df_pdr = pd.DataFrame(self.pdr_estimates)
        df_pdr['timestamp'] = pd.to_datetime(df_pdr['timestamp'])
        df_pdr = df_pdr.set_index('timestamp')
        
        df_yaw = pd.DataFrame(self.yaw_angles)
        df_yaw['timestamp'] = pd.to_datetime(df_yaw['timestamp'], unit='s')
        df_yaw = df_yaw.set_index('timestamp')
        
        merged_df = pd.merge_asof(df_pdr, df_yaw, left_index=True, right_index=True, 
                                 tolerance=pd.Timedelta('0.02s'), direction='nearest')
        
        last_est_ts = pd.to_datetime(self.last_estimate_ts, unit='s')
        
        filtered_df = merged_df[merged_df.index > last_est_ts]
        
        if len(filtered_df) == 0:
            return self.last_est
        
        x, y, yaw = self.last_est
        
        for idx, row in filtered_df.iterrows():
            if pd.notna(row['velocity']) and pd.notna(row['yaw']):
                if idx == filtered_df.index[0]:
                    dt = (idx - last_est_ts).total_seconds()
                else:
                    prev_idx = filtered_df.index[filtered_df.index < idx][-1]
                    dt = (idx - prev_idx).total_seconds()
                
                velocity = row['velocity']
                
                dx = velocity * np.cos(yaw) * dt
                dy = velocity * np.sin(yaw) * dt
                
                x += dx
                y += dy
                yaw += row['dyaw']
        
        est = (x, y, wrap_angle_pi(yaw))
        return est

    def predict_by_vio(self):
        if self.last_estimate_ts is None or len(self.vio_estimates) == 0:
            return self.last_est
        
        x, y, yaw = self.last_est
        
        for vio_data in self.vio_estimates:
            if vio_data["timestamp"] > self.last_estimate_ts:
                cos_yaw = np.cos(yaw)
                sin_yaw = np.sin(yaw)
                
                global_dx = cos_yaw * vio_data["dx"] - sin_yaw * vio_data["dy"]
                global_dy = sin_yaw * vio_data["dx"] + cos_yaw * vio_data["dy"]
                
                x += global_dx
                y += global_dy
                yaw += vio_data["dyaw"]
        
        est = (x, y, wrap_angle_pi(yaw))
        return est
        
    def update_location_by_tag(self):
        if len(self.uwbt_data) == 0:
            return self.last_est
            
        latest_uwbt = self.uwbt_data[-1]
        tag_id = latest_uwbt["tag_id"]
        
        if latest_uwbt["sensor_timestamp"] > self.last_estimate_ts:
            print(tag_id)
            
            tag_loc, tag_q = self.get_latest_tag_pose(tag_id)
            print(tag_loc, tag_q)

            if tag_loc is not None:
                local_point = spherical_to_cartesian(latest_uwbt["distance"], latest_uwbt["aoa_azimuth"], latest_uwbt["aoa_elevation"])
                R = Rotation.from_quat(tag_q)
                global_point = R.apply(local_point) + tag_loc
                
                est = (float(global_point[0]), float(global_point[1]), self.last_est[2])
                return est
            
        return self.last_est
            
    def estimate_location(self):
        if self.state != LocStatus.INITIALIZED:
            return self.last_est
        
        vio_available = (self.vio_estimates[-1]["timestamp"] > self.last_estimate_ts)

        if vio_available:
            print("vio available")
            est = self.predict_by_vio()
        else:
            print("vio not available")
            est = self.predict_by_pdr()
            
        self.last_est = est
        
        if len(self.uwbt_data) > 0:
            est = self.update_location_by_tag()
        
        self.last_est = est
        
        self.last_estimate_ts = self.newest_data_ts
        
        return est
    

def split_lines(r):
    if False and r.headers['content-type'].startswith("application/x-xz"):
        l = lzma.decompress(r.content).decode('ascii').splitlines()
    else:
        l = r.text.splitlines()
    return l


def do_req (req, n=2):
    r = requests.get(server+trialname+req)
    print("\n==>  GET " + req + " --> " + str(r.status_code))
    l = split_lines(r)
    if len(l) <= 2*n+1:
        print(r.text + '\n')
    else:
        print('\n'.join(l[:n]
                        + ["   ... ___%d lines omitted___ ...   " % len(l)]
                        + l[-n:] + [""]))
    
    return r


def parse_data(sensor_type, data_row):
    
    # Column names for each sensor type
    columns = {
        'ACCE': ['app_timestamp', 'sensor_timestamp', 'acc_x', 'acc_y', 'acc_z', 'accuracy'],
        'GYRO': ['app_timestamp', 'sensor_timestamp', 'gyr_x', 'gyr_y', 'gyr_z', 'accuracy'],
        'MAGN': ['app_timestamp', 'sensor_timestamp', 'mag_x', 'mag_y', 'mag_z', 'accuracy'],
        'AHRS': ['app_timestamp', 'sensor_timestamp', 'pitch_x', 'roll_y', 'yaw_z', 'quat_2', 'quat_3', 'quat_4', 'accuracy'],
        'UWBP': ['app_timestamp', 'sensor_timestamp', 'tag_id', 'distance', 'direction_vec_x', 'direction_vec_y', 'direction_vec_z'],
        'UWBT': ['app_timestamp', 'sensor_timestamp', 'tag_id', 'distance', 'aoa_azimuth', 'aoa_elevation', 'nlos'],
        'GPOS': ['app_timestamp', 'sensor_timestamp', 'object_id', 'location_x', 'location_y', 'location_z', 'quat_x', 'quat_y', 'quat_z'],
        'VISO': ['app_timestamp', 'sensor_timestamp', 'location_x', 'location_y', 'location_z', 'quat_x', 'quat_y', 'quat_z']
    }
    if sensor_type not in columns: 
        return None
    
    row_dict = {}
    for i, col_name in enumerate(columns[sensor_type]):
        if i < len(data_row):  # Ensure we don't go out of bounds
            # Convert numeric values to float, except for specific ID fields
            if col_name not in ['tag_id', 'object_id']:
                try:
                    row_dict[col_name] = float(data_row[i])
                except (ValueError, TypeError):
                    row_dict[col_name] = data_row[i]
            else:
                row_dict[col_name] = data_row[i]
    return row_dict


def process_data(localizer, recv_data):
    recv_sensor_lines = split_lines(recv_data)

    # Dictionary to store structured data
    sensor_dictionaries = {}
    
    for line in recv_sensor_lines:
        # Skip empty lines
        if not line.strip():
            continue
        
        # Split the line by semicolon
        parts = line.strip().split(';')
        
        # Get sensor type (first part of the line)
        sensor_type = parts[0]
        data_row = parts[1:]
    
        row_dict = parse_data(sensor_type, data_row)
        if row_dict is not None:
                    
            localizer.callback(sensor_type, row_dict)
    
    est = localizer.estimate_location()
    
    return est 


def demo (maxw, output_csv):
    pdr_model = SimplePDR()
    localizer = DemoLocalizer(pdr_model=pdr_model)

    ## First of all, reload
    r = do_req("/reload")

    ## Check initial state
    r = do_req("/state")
    s = parse(statefmt, r.text); print(s.named)

    ## Get first 0.5s worth of data
    time.sleep(maxw)
    r = do_req("/nextdata?horizon=0.5")
    est = process_data(localizer, r)
    print("---")
    print(est)

    ## Look at remaining time
    time.sleep(maxw)
    r = do_req("/state")
    s = parse(statefmt, r.text); print(s.named)
    
    ## Set estimates
    time.sleep(maxw)
    while True:
        r = do_req("/nextdata?position=%.1f,%.1f,%.1f" % (est[0], est[1], est[2]))
        est = process_data(localizer, r)
        print("---")
        print(est)
        time.sleep(maxw)
        if r.status_code == 405:
            break # end of competition data

    ## Get estimates
    r = do_req("/estimates", 3)
    result = []
    for l in r.text.splitlines()[2:]: # ignore first sample (given origin)
        print(l)
        s = parse(estfmt, l); 
        x, y, yaw = s.named["pos"].split(",")
        result.append({"timestamp" : s.named["pts"], "x": x, "y": y, "yaw": yaw})
    df = pd.DataFrame(result)
    print(df)

    df.to_csv(output_csv, index=False)

    ## Get log
    time.sleep(maxw)
    r = do_req("/log", 12)

    ## We finish here
    print("Demo stops here")

################################################################

if __name__ == '__main__':
        
    if len(sys.argv) != 4:
        print("""A demo for the EvAAL API.  Usage is
                %s [trial] [server]

                if omitted, TRIAL defaults to '%s' and SERVER to %s""" %
              (sys.argv[0], trialname, server))
    else:
        trialname = sys.argv[1]
        server = sys.argv[2]
        output_csv = sys.argv[3]
        
    maxw = 0.5 # set this value to 0.0 to run at maximum speed
    demo(maxw, output_csv)
    exit(0)
