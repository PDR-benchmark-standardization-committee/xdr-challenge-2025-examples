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
        self.position_history = []
        
        # Setup dashboard
        self.setup_dashboard()
        
    def setup_dashboard(self):
        # Create figure and subplots
        self.fig = plt.figure(figsize=(15, 10))
        self.fig.suptitle('Localization Dashboard', fontsize=16)
        
        # Create grid layout
        gs = GridSpec(4, 4, figure=self.fig)
        
        # Position plot (2D map)
        self.position_ax = self.fig.add_subplot(gs[0:2, 0:2])
        self.position_ax.set_title('Position Tracking')
        self.position_ax.set_xlabel('X Position (m)')
        self.position_ax.set_ylabel('Y Position (m)')
        self.position_ax.grid(True)
        self.position_line, = self.position_ax.plot([], [], 'ro-', label='Trajectory')
        self.position_point, = self.position_ax.plot([], [], 'bo', markersize=10, label='Current Position')
        self.position_ax.legend()
        
        # Accelerometer data
        self.acce_ax = self.fig.add_subplot(gs[0, 2])
        self.acce_ax.set_title('Accelerometer')
        self.acce_ax.set_xlabel('Time')
        self.acce_ax.set_ylabel('Acceleration (m/s²)')
        self.acce_ax.grid(True)
        self.acce_lines = {
            'x': self.acce_ax.plot([], [], 'r-', label='X')[0],
            'y': self.acce_ax.plot([], [], 'g-', label='Y')[0],
            'z': self.acce_ax.plot([], [], 'b-', label='Z')[0]
        }
        self.acce_ax.legend()
        
        # Gyroscope data
        self.gyro_ax = self.fig.add_subplot(gs[0, 3])
        self.gyro_ax.set_title('Gyroscope')
        self.gyro_ax.set_xlabel('Time')
        self.gyro_ax.set_ylabel('Angular Velocity (rad/s)')
        self.gyro_ax.grid(True)
        self.gyro_lines = {
            'x': self.gyro_ax.plot([], [], 'r-', label='X')[0],
            'y': self.gyro_ax.plot([], [], 'g-', label='Y')[0],
            'z': self.gyro_ax.plot([], [], 'b-', label='Z')[0]
        }
        self.gyro_ax.legend()
        
        # Magnetometer data
        self.magn_ax = self.fig.add_subplot(gs[1, 2])
        self.magn_ax.set_title('Magnetometer')
        self.magn_ax.set_xlabel('Time')
        self.magn_ax.set_ylabel('Magnetic Field (μT)')
        self.magn_ax.grid(True)
        self.magn_lines = {
            'x': self.magn_ax.plot([], [], 'r-', label='X')[0],
            'y': self.magn_ax.plot([], [], 'g-', label='Y')[0],
            'z': self.magn_ax.plot([], [], 'b-', label='Z')[0]
        }
        self.magn_ax.legend()
        
        # AHRS data
        self.ahrs_ax = self.fig.add_subplot(gs[1, 3])
        self.ahrs_ax.set_title('Orientation (AHRS)')
        self.ahrs_ax.set_xlabel('Time')
        self.ahrs_ax.set_ylabel('Angle (degrees)')
        self.ahrs_ax.grid(True)
        self.ahrs_lines = {
            'pitch': self.ahrs_ax.plot([], [], 'r-', label='Pitch')[0],
            'roll': self.ahrs_ax.plot([], [], 'g-', label='Roll')[0],
            'yaw': self.ahrs_ax.plot([], [], 'b-', label='Yaw')[0]
        }
        self.ahrs_ax.legend()
        
        # UWB data
        self.uwb_ax = self.fig.add_subplot(gs[2, 0])
        self.uwb_ax.set_title('UWB Distance')
        self.uwb_ax.set_xlabel('Time')
        self.uwb_ax.set_ylabel('Distance (m)')
        self.uwb_ax.grid(True)
        self.uwb_distance_line, = self.uwb_ax.plot([], [], 'r-', label='Distance')
        self.uwb_ax.legend()
        
        # UWB angle data
        self.uwb_angle_ax = self.fig.add_subplot(gs[2, 1])
        self.uwb_angle_ax.set_title('UWB Angle of Arrival')
        self.uwb_angle_ax.set_xlabel('Time')
        self.uwb_angle_ax.set_ylabel('Angle (degrees)')
        self.uwb_angle_ax.grid(True)
        self.uwb_angle_lines = {
            'azimuth': self.uwb_angle_ax.plot([], [], 'r-', label='Azimuth')[0],
            'elevation': self.uwb_angle_ax.plot([], [], 'g-', label='Elevation')[0]
        }
        self.uwb_angle_ax.legend()
        
        # Tag position data
        self.tag_ax = self.fig.add_subplot(gs[2, 2:])
        self.tag_ax.set_title('Tag Position')
        self.tag_ax.set_xlabel('X Position (m)')
        self.tag_ax.set_ylabel('Y Position (m)')
        self.tag_ax.grid(True)
        self.tag_scatter = self.tag_ax.scatter([], [], c=[], cmap='viridis', s=100, label='Tags')
        self.tag_ax.legend()
        
        # Visual Odometry data
        self.viso_ax = self.fig.add_subplot(gs[3, 0:2])
        self.viso_ax.set_title('Visual Odometry')
        self.viso_ax.set_xlabel('X Position (m)')
        self.viso_ax.set_ylabel('Y Position (m)')
        self.viso_ax.grid(True)
        self.viso_line, = self.viso_ax.plot([], [], 'g-', label='VO Path')
        self.viso_point, = self.viso_ax.plot([], [], 'go', markersize=10, label='Current VO Position')
        self.viso_ax.legend()
        
        # Estimated vs Ground Truth comparison
        self.error_ax = self.fig.add_subplot(gs[3, 2:])
        self.error_ax.set_title('Position Estimation Error')
        self.error_ax.set_xlabel('Time')
        self.error_ax.set_ylabel('Error (m)')
        self.error_ax.grid(True)
        self.error_line, = self.error_ax.plot([], [], 'r-', label='Error')
        self.error_ax.legend()
        
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        
        # Initialize data buffers for plotting
        self.max_data_points = 100  # Maximum number of points to display
        self.timestamps = deque(maxlen=self.max_data_points)
        
        # Data for each sensor
        self.acce_buffer = {
            'x': deque(maxlen=self.max_data_points),
            'y': deque(maxlen=self.max_data_points),
            'z': deque(maxlen=self.max_data_points)
        }
        
        self.gyro_buffer = {
            'x': deque(maxlen=self.max_data_points),
            'y': deque(maxlen=self.max_data_points),
            'z': deque(maxlen=self.max_data_points)
        }
        
        self.magn_buffer = {
            'x': deque(maxlen=self.max_data_points),
            'y': deque(maxlen=self.max_data_points),
            'z': deque(maxlen=self.max_data_points)
        }
        
        self.ahrs_buffer = {
            'pitch': deque(maxlen=self.max_data_points),
            'roll': deque(maxlen=self.max_data_points),
            'yaw': deque(maxlen=self.max_data_points)
        }
        
        self.uwb_distance_buffer = deque(maxlen=self.max_data_points)
        self.uwb_angle_buffer = {
            'azimuth': deque(maxlen=self.max_data_points),
            'elevation': deque(maxlen=self.max_data_points)
        }
        
        self.tag_positions = {}
        self.viso_positions = deque(maxlen=self.max_data_points)
        self.error_buffer = deque(maxlen=self.max_data_points)
        
        # Start animation in a non-blocking way
        self.ani = animation.FuncAnimation(
            self.fig, self.update_dashboard, interval=500, blit=False)
        plt.ion()  # Turn on interactive mode
        plt.show(block=False)
        
    def update_dashboard(self, frame):
        # Update position plot
        if self.position_history:
            x_positions = [pos[0] for pos in self.position_history]
            y_positions = [pos[1] for pos in self.position_history]
            
            self.position_line.set_data(x_positions, y_positions)
            if x_positions:
                self.position_point.set_data([x_positions[-1]], [y_positions[-1]])
            
            # Update position plot limits
            if x_positions and y_positions:
                x_margin = max(1, 0.1 * (max(x_positions) - min(x_positions) if len(x_positions) > 1 else 1))
                y_margin = max(1, 0.1 * (max(y_positions) - min(y_positions) if len(y_positions) > 1 else 1))
                self.position_ax.set_xlim(min(x_positions) - x_margin, max(x_positions) + x_margin)
                self.position_ax.set_ylim(min(y_positions) - y_margin, max(y_positions) + y_margin)
        
        # Update accelerometer plot
        x_data = list(self.acce_buffer['x'])
        y_data = list(self.acce_buffer['y'])
        z_data = list(self.acce_buffer['z'])
        t_data = list(range(len(x_data)))
        
        if t_data:
            self.acce_lines['x'].set_data(t_data, x_data)
            self.acce_lines['y'].set_data(t_data, y_data)
            self.acce_lines['z'].set_data(t_data, z_data)
            
            if x_data or y_data or z_data:
                min_val = min(min(x_data) if x_data else 0, min(y_data) if y_data else 0, min(z_data) if z_data else 0)
                max_val = max(max(x_data) if x_data else 0, max(y_data) if y_data else 0, max(z_data) if z_data else 0)
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.acce_ax.set_xlim(0, len(t_data))
                self.acce_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update gyroscope plot
        x_data = list(self.gyro_buffer['x'])
        y_data = list(self.gyro_buffer['y'])
        z_data = list(self.gyro_buffer['z'])
        t_data = list(range(len(x_data)))
        
        if t_data:
            self.gyro_lines['x'].set_data(t_data, x_data)
            self.gyro_lines['y'].set_data(t_data, y_data)
            self.gyro_lines['z'].set_data(t_data, z_data)
            
            if x_data or y_data or z_data:
                min_val = min(min(x_data) if x_data else 0, min(y_data) if y_data else 0, min(z_data) if z_data else 0)
                max_val = max(max(x_data) if x_data else 0, max(y_data) if y_data else 0, max(z_data) if z_data else 0)
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.gyro_ax.set_xlim(0, len(t_data))
                self.gyro_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update magnetometer plot
        x_data = list(self.magn_buffer['x'])
        y_data = list(self.magn_buffer['y'])
        z_data = list(self.magn_buffer['z'])
        t_data = list(range(len(x_data)))
        
        if t_data:
            self.magn_lines['x'].set_data(t_data, x_data)
            self.magn_lines['y'].set_data(t_data, y_data)
            self.magn_lines['z'].set_data(t_data, z_data)
            
            if x_data or y_data or z_data:
                min_val = min(min(x_data) if x_data else 0, min(y_data) if y_data else 0, min(z_data) if z_data else 0)
                max_val = max(max(x_data) if x_data else 0, max(y_data) if y_data else 0, max(z_data) if z_data else 0)
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.magn_ax.set_xlim(0, len(t_data))
                self.magn_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update AHRS plot
        pitch_data = list(self.ahrs_buffer['pitch'])
        roll_data = list(self.ahrs_buffer['roll'])
        yaw_data = list(self.ahrs_buffer['yaw'])
        t_data = list(range(len(pitch_data)))
        
        if t_data:
            self.ahrs_lines['pitch'].set_data(t_data, pitch_data)
            self.ahrs_lines['roll'].set_data(t_data, roll_data)
            self.ahrs_lines['yaw'].set_data(t_data, yaw_data)
            
            if pitch_data or roll_data or yaw_data:
                min_val = min(min(pitch_data) if pitch_data else 0, 
                              min(roll_data) if roll_data else 0, 
                              min(yaw_data) if yaw_data else 0)
                max_val = max(max(pitch_data) if pitch_data else 0, 
                              max(roll_data) if roll_data else 0, 
                              max(yaw_data) if yaw_data else 0)
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.ahrs_ax.set_xlim(0, len(t_data))
                self.ahrs_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update UWB distance plot
        distance_data = list(self.uwb_distance_buffer)
        t_data = list(range(len(distance_data)))
        
        if t_data:
            self.uwb_distance_line.set_data(t_data, distance_data)
            
            if distance_data:
                min_val = min(distance_data) if distance_data else 0
                max_val = max(distance_data) if distance_data else 0
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.uwb_ax.set_xlim(0, len(t_data))
                self.uwb_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update UWB angle plot
        azimuth_data = list(self.uwb_angle_buffer['azimuth'])
        elevation_data = list(self.uwb_angle_buffer['elevation'])
        t_data = list(range(len(azimuth_data)))
        
        if t_data:
            self.uwb_angle_lines['azimuth'].set_data(t_data, azimuth_data)
            self.uwb_angle_lines['elevation'].set_data(t_data, elevation_data)
            
            if azimuth_data or elevation_data:
                min_val = min(min(azimuth_data) if azimuth_data else 0, 
                              min(elevation_data) if elevation_data else 0)
                max_val = max(max(azimuth_data) if azimuth_data else 0, 
                              max(elevation_data) if elevation_data else 0)
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.uwb_angle_ax.set_xlim(0, len(t_data))
                self.uwb_angle_ax.set_ylim(min_val - margin, max_val + margin)
        
        # Update tag position plot
        if self.tag_positions:
            tag_ids = list(self.tag_positions.keys())
            x_positions = [self.tag_positions[tag_id][0] for tag_id in tag_ids]
            y_positions = [self.tag_positions[tag_id][1] for tag_id in tag_ids]
            colors = np.arange(len(tag_ids))
            
            self.tag_scatter.set_offsets(np.column_stack((x_positions, y_positions)))
            self.tag_scatter.set_array(np.array(colors))
            
            # Update tag plot limits
            if x_positions and y_positions:
                x_margin = max(1, 0.1 * (max(x_positions) - min(x_positions) if len(x_positions) > 1 else 1))
                y_margin = max(1, 0.1 * (max(y_positions) - min(y_positions) if len(y_positions) > 1 else 1))
                self.tag_ax.set_xlim(min(x_positions) - x_margin, max(x_positions) + x_margin)
                self.tag_ax.set_ylim(min(y_positions) - y_margin, max(y_positions) + y_margin)
        
        # Update Visual Odometry plot
        if self.viso_positions:
            x_positions = [pos[0] for pos in self.viso_positions]
            y_positions = [pos[1] for pos in self.viso_positions]
            
            self.viso_line.set_data(x_positions, y_positions)
            if x_positions:
                self.viso_point.set_data([x_positions[-1]], [y_positions[-1]])
            
            # Update viso plot limits
            if x_positions and y_positions:
                x_margin = max(1, 0.1 * (max(x_positions) - min(x_positions) if len(x_positions) > 1 else 1))
                y_margin = max(1, 0.1 * (max(y_positions) - min(y_positions) if len(y_positions) > 1 else 1))
                self.viso_ax.set_xlim(min(x_positions) - x_margin, max(x_positions) + x_margin)
                self.viso_ax.set_ylim(min(y_positions) - y_margin, max(y_positions) + y_margin)
        
        # Update error plot
        error_data = list(self.error_buffer)
        t_data = list(range(len(error_data)))
        
        if t_data:
            self.error_line.set_data(t_data, error_data)
            
            if error_data:
                min_val = min(error_data) if error_data else 0
                max_val = max(error_data) if error_data else 0
                margin = max(0.1, 0.1 * (max_val - min_val) if max_val != min_val else 1)
                
                self.error_ax.set_xlim(0, len(t_data))
                self.error_ax.set_ylim(0, max_val + margin)  # Error should always be >= 0
        
        # Draw the figure
        self.fig.canvas.draw_idle()
        plt.pause(0.01)
        
        return (self.position_line, self.position_point, 
                self.acce_lines['x'], self.acce_lines['y'], self.acce_lines['z'],
                self.gyro_lines['x'], self.gyro_lines['y'], self.gyro_lines['z'],
                self.magn_lines['x'], self.magn_lines['y'], self.magn_lines['z'],
                self.ahrs_lines['pitch'], self.ahrs_lines['roll'], self.ahrs_lines['yaw'],
                self.uwb_distance_line, 
                self.uwb_angle_lines['azimuth'], self.uwb_angle_lines['elevation'],
                self.tag_scatter,
                self.viso_line, self.viso_point,
                self.error_line)
    
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
        # Update dashboard buffers
        if 'acc_x' in data and 'acc_y' in data and 'acc_z' in data:
            self.acce_buffer['x'].append(data['acc_x'])
            self.acce_buffer['y'].append(data['acc_y'])
            self.acce_buffer['z'].append(data['acc_z'])

    def callback_gyro(self, data):
        self.gyro_data.append(data)
        # Update dashboard buffers
        if 'gyr_x' in data and 'gyr_y' in data and 'gyr_z' in data:
            self.gyro_buffer['x'].append(data['gyr_x'])
            self.gyro_buffer['y'].append(data['gyr_y'])
            self.gyro_buffer['z'].append(data['gyr_z'])
        
    def callback_magn(self, data):
        self.magn_data.append(data)
        # Update dashboard buffers
        if 'mag_x' in data and 'mag_y' in data and 'mag_z' in data:
            self.magn_buffer['x'].append(data['mag_x'])
            self.magn_buffer['y'].append(data['mag_y'])
            self.magn_buffer['z'].append(data['mag_z'])
        
    def callback_ahrs(self, data):
        self.ahrs_data.append(data)
        # Update dashboard buffers
        if 'pitch_x' in data and 'roll_y' in data and 'yaw_z' in data:
            self.ahrs_buffer['pitch'].append(data['pitch_x'])
            self.ahrs_buffer['roll'].append(data['roll_y'])
            self.ahrs_buffer['yaw'].append(data['yaw_z'])
        
    def callback_uwbp(self, data):
        self.uwbp_data.append(data)
        # Update dashboard buffers
        if 'distance' in data:
            self.uwb_distance_buffer.append(data['distance'])
        
    def callback_uwbt(self, data):
        self.uwbt_data.append(data)
        # Update dashboard buffers
        if 'distance' in data:
            self.uwb_distance_buffer.append(data['distance'])
        if 'aoa_azimuth' in data and 'aoa_elevation' in data:
            self.uwb_angle_buffer['azimuth'].append(data['aoa_azimuth'])
            self.uwb_angle_buffer['elevation'].append(data['aoa_elevation'])
        
    def callback_gpos(self, data):
        self.gpos_data.append(data)
        # Update dashboard buffers - store tag positions
        if 'object_id' in data and 'location_x' in data and 'location_y' in data:
            tag_id = data['object_id']
            x = data['location_x']
            y = data['location_y']
            z = data['location_z']
            self.tag_positions[tag_id] = (x, y, z)
        
    def callback_viso(self, data):
        self.viso_data.append(data)
        # Update dashboard buffers
        if 'location_x' in data and 'location_y' in data and 'location_z' in data:
            self.viso_positions.append((data['location_x'], data['location_y'], data['location_z']))
        
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
            
            # Update position history for plotting
            self.position_history.append(est)
            
            # Calculate position error if we have ground truth
            if self.viso_positions:
                latest_viso = self.viso_positions[-1]
                error = np.sqrt((est[0] - latest_viso[0])**2 + (est[1] - latest_viso[1])**2)
                self.error_buffer.append(error)
        
        # Update the dashboard
        plt.pause(0.01)  # Allow time for the plot to update
        
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
    for pos in range(1000):
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
