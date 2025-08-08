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
from scipy.spatial.transform import Rotation

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


class DemoLocalizer:
    def __init__(self):
        self.acce_data = []
        self.gyro_data = []
        self.magn_data = []
        self.ahrs_data = []
        self.uwbp_data = []
        self.uwbt_data = []
        self.gpos_data = []
        self.viso_data = []
        self.last_est = (0, 0, 0)
        
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
    
    def callback_acce(self, data):
        self.acce_data.append(data)

    def callback_gyro(self, data):
        self.gyro_data.append(data)
        
    def callback_magn(self, data):
        self.magn_data.append(data)
        
    def callback_ahrs(self, data):
        self.ahrs_data.append(data)
        
    def callback_uwbp(self, data):
        self.uwbp_data.append(data)
        
    def callback_uwbt(self, data):
        self.uwbt_data.append(data)
        
    def callback_gpos(self, data):
        self.gpos_data.append(data)
        
    def callback_viso(self, data):
        self.viso_data.append(data)
        
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
        for d in self.gpos_data:
            if d["object_id"] == tag_id:
                latest_gpos = d
                break
            
        loc = None
        q = None
        if latest_gpos is not None:            
            loc = np.array((latest_gpos["location_x"], latest_gpos["location_y"], latest_gpos["location_z"]))
            quat_w = np.sqrt(1 - (latest_gpos["quat_x"]**2 + latest_gpos["quat_y"]**2 + latest_gpos["quat_z"]**2))
            q = np.array([latest_gpos["quat_x"], latest_gpos["quat_y"], latest_gpos["quat_z"], quat_w])
        return loc, q

    def estimate_location(self):
        # estimate location using UWB AoA + Ranging
        
        latest_uwbt = self.uwbt_data[-1] # get latest UWB tag data
        tag_id = latest_uwbt["tag_id"]
        print(tag_id)
        
        tag_loc, tag_q = self.get_latest_tag_pose(tag_id) # get latest pose of corresponding tag
        print(tag_loc, tag_q)

        est = self.last_est        
        if tag_loc is not None:
            # Convert AoA + Distance measurement into global position using tag's pose
            local_point = spherical_to_cartesian(latest_uwbt["distance"], latest_uwbt["aoa_azimuth"], latest_uwbt["aoa_elevation"])
            R = Rotation.from_quat(tag_q)
            global_point = R.apply(local_point) + tag_loc
            
            est = (float(global_point[0]), float(global_point[1]), 0) # angle should be estimated 
            
            self.last_est = est
        
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
        'AHRS': ['app_timestamp', 'sensor_timestamp', 'pitch_x', 'roll_y', 'yaw_z', 'quat_2', 'quat_3', 'quat_4', 'quat_w', 'accuracy'],
        'UWBP': ['app_timestamp', 'sensor_timestamp', 'tag_id', 'distance', 'direction_vec_x', 'direction_vec_y', 'direction_vec_z'],
        'UWBT': ['app_timestamp', 'sensor_timestamp', 'tag_id', 'distance', 'aoa_azimuth', 'aoa_elevation', 'nlos'],
        'GPOS': ['app_timestamp', 'sensor_timestamp', 'object_id', 'location_x', 'location_y', 'location_z', 'quat_x', 'quat_y', 'quat_z', 'quat_w'],
        'VISO': ['app_timestamp', 'sensor_timestamp', 'location_x', 'location_y', 'location_z', 'quat_x', 'quat_y', 'quat_z', 'quat_w']
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


def demo (maxw):
    localizer = DemoLocalizer()

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
    for pos in range(20):
        r = do_req("/nextdata?position=%.1f,%.1f,%.1f" % (est[0], est[1], est[2]))
        est = process_data(localizer, r)
        print("---")
        print(est)
        time.sleep(maxw)

    ## Get estimates
    r = do_req("/estimates", 3)
    s = parse(estfmt, r.text.splitlines()[-1]); print(s.named)

    ## Get log
    time.sleep(maxw)
    r = do_req("/log", 12)

    ## We finish here
    print("Demo stops here")

################################################################

if __name__ == '__main__':
        
    if len(sys.argv) != 3:
        print("""A demo for the EvAAL API.  Usage is
                %s [trial] [server]

                if omitted, TRIAL defaults to '%s' and SERVER to %s""" %
              (sys.argv[0], trialname, server))
    else:
        trialname = sys.argv[1]
        server = sys.argv[2]
    maxw = 0.5
    demo(maxw)
    exit(0)
