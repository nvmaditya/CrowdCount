"""
Zone Manager - Interactive tool to create and edit polygonal zones
Allows drawing zones on video frames and saving to zones.json
"""

import cv2
import numpy as np
import json
from pathlib import Path


class ZoneManager:
    def __init__(self, zones_file='zones.json'):
        """
        Initialize the zone manager
        
        Args:
            zones_file: Path to zones configuration JSON file
        """
        self.zones_file = zones_file
        self.zones = self.load_zones()
        self.current_zone_points = []
        self.drawing = False
        self.current_frame = None
        self.zone_colors = [
            (0, 255, 0),    # Green
            (255, 0, 0),    # Blue
            (0, 0, 255),    # Red
            (255, 255, 0),  # Cyan
            (255, 0, 255),  # Magenta
            (0, 255, 255),  # Yellow
        ]
        
    def load_zones(self):
        """Load zones from JSON file"""
        zones_path = Path(self.zones_file)
        if zones_path.exists():
            with open(zones_path, 'r') as f:
                return json.load(f)
        return {"zones": []}
    
    def save_zones(self):
        """Save zones to JSON file"""
        with open(self.zones_file, 'w') as f:
            json.dump(self.zones, f, indent=2)
        print(f"Zones saved to {self.zones_file}")
    
    def draw_zones(self, frame):
        """Draw all existing zones on the frame"""
        overlay = frame.copy()
        
        for idx, zone in enumerate(self.zones.get('zones', [])):
            points = np.array(zone['points'], dtype=np.int32)
            color = tuple(zone.get('color', [0, 255, 0]))
            
            # Draw filled polygon with transparency
            cv2.fillPoly(overlay, [points], color)
            
            # Draw polygon border
            cv2.polylines(frame, [points], True, color, 2)
            
            # Draw zone vertices
            for point in points:
                cv2.circle(frame, tuple(point), 5, color, -1)
            
            # Draw zone name and index
            if 'name' in zone:
                centroid = points.mean(axis=0).astype(int)
                label = f"{idx}: {zone['name']}"
                cv2.putText(frame, label, tuple(centroid),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        
        # Blend overlay with frame for transparency effect
        cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)
        
        return frame
    
    def draw_current_zone(self, frame):
        """Draw the zone currently being created"""
        if len(self.current_zone_points) > 0:
            # Draw points
            for point in self.current_zone_points:
                cv2.circle(frame, tuple(point), 5, (0, 255, 255), -1)
            
            # Draw lines connecting points
            if len(self.current_zone_points) > 1:
                points = np.array(self.current_zone_points, dtype=np.int32)
                cv2.polylines(frame, [points], False, (0, 255, 255), 2)
            
            # Draw line from last point to cursor (if drawing)
            if self.drawing and len(self.current_zone_points) > 0:
                pass  # Will be handled by mouse callback
        
        return frame
    
    def mouse_callback(self, event, x, y, flags, param):
        """Handle mouse events for zone creation"""
        if event == cv2.EVENT_LBUTTONDOWN:
            # Add point to current zone
            self.current_zone_points.append([x, y])
            print(f"Point added: ({x}, {y}) - Total points: {len(self.current_zone_points)}")
            
        elif event == cv2.EVENT_RBUTTONDOWN:
            # Finish current zone
            if len(self.current_zone_points) >= 3:
                self.finish_zone()
            else:
                print("Need at least 3 points to create a zone!")
        
        elif event == cv2.EVENT_MBUTTONDOWN:
            # Remove last point
            if len(self.current_zone_points) > 0:
                removed = self.current_zone_points.pop()
                print(f"Removed last point: {removed}")
    
    def finish_zone(self):
        """Finish creating the current zone and add it to zones list"""
        zone_name = input("\nEnter zone name: ")
        
        # Get color for this zone
        color_idx = len(self.zones['zones']) % len(self.zone_colors)
        color = self.zone_colors[color_idx]
        
        # Create zone dictionary
        new_zone = {
            'name': zone_name,
            'points': self.current_zone_points,
            'color': list(color),
            'enabled': True
        }
        
        self.zones['zones'].append(new_zone)
        print(f"Zone '{zone_name}' created with {len(self.current_zone_points)} points")
        
        # Reset current zone
        self.current_zone_points = []
    
    def delete_zone(self, zone_index):
        """Delete a zone by index"""
        if 0 <= zone_index < len(self.zones['zones']):
            removed = self.zones['zones'].pop(zone_index)
            print(f"Deleted zone: {removed.get('name', 'Unnamed')}")
        else:
            print(f"Invalid zone index: {zone_index}")
    
    def toggle_zone(self, zone_index):
        """Enable/disable a zone by index"""
        if 0 <= zone_index < len(self.zones['zones']):
            zone = self.zones['zones'][zone_index]
            zone['enabled'] = not zone.get('enabled', True)
            status = "enabled" if zone['enabled'] else "disabled"
            print(f"Zone '{zone.get('name', 'Unnamed')}' {status}")
        else:
            print(f"Invalid zone index: {zone_index}")
    
    def list_zones(self):
        """Print list of all zones"""
        print("\n=== Current Zones ===")
        if not self.zones['zones']:
            print("No zones defined")
        else:
            for idx, zone in enumerate(self.zones['zones']):
                status = "enabled" if zone.get('enabled', True) else "disabled"
                points_count = len(zone.get('points', []))
                print(f"{idx}: {zone.get('name', 'Unnamed')} ({points_count} points, {status})")
        print("====================\n")
    
    def edit_zones_interactive(self, video_source=0):
        """
        Interactive zone editor using video feed
        
        Args:
            video_source: Video file path or camera index (0 for webcam)
        """
        cap = cv2.VideoCapture(video_source)
        
        if not cap.isOpened():
            print(f"Error: Cannot open video source {video_source}")
            return
        
        window_name = 'Zone Editor'
        cv2.namedWindow(window_name)
        cv2.setMouseCallback(window_name, self.mouse_callback)
        
        print("\n=== Zone Editor ===")
        print("Controls:")
        print("  Left Click: Add point to zone")
        print("  Right Click: Finish current zone")
        print("  Middle Click: Remove last point")
        print("  's': Save zones to file")
        print("  'c': Clear current zone")
        print("  'd': Delete zone (enter index)")
        print("  't': Toggle zone on/off (enter index)")
        print("  'l': List all zones")
        print("  'q': Quit")
        print("===================\n")
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    # If video ends, loop back to start
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                
                self.current_frame = frame.copy()
                
                # Draw existing zones
                display_frame = self.draw_zones(frame.copy())
                
                # Draw current zone being created
                display_frame = self.draw_current_zone(display_frame)
                
                # Add instructions
                instructions = f"Points: {len(self.current_zone_points)}"
                cv2.putText(display_frame, instructions, (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                cv2.imshow(window_name, display_frame)
                
                key = cv2.waitKey(1) & 0xFF
                
                if key == ord('q'):
                    print("Quitting zone editor...")
                    break
                    
                elif key == ord('s'):
                    self.save_zones()
                    
                elif key == ord('c'):
                    self.current_zone_points = []
                    print("Current zone cleared")
                    
                elif key == ord('d'):
                    self.list_zones()
                    try:
                        idx = int(input("Enter zone index to delete: "))
                        self.delete_zone(idx)
                    except (ValueError, KeyboardInterrupt):
                        print("Cancelled")
                        
                elif key == ord('t'):
                    self.list_zones()
                    try:
                        idx = int(input("Enter zone index to toggle: "))
                        self.toggle_zone(idx)
                    except (ValueError, KeyboardInterrupt):
                        print("Cancelled")
                        
                elif key == ord('l'):
                    self.list_zones()
        
        finally:
            cap.release()
            cv2.destroyAllWindows()
    
    def create_zone_from_coordinates(self, name, points, color=None, enabled=True):
        """
        Programmatically create a zone from coordinates
        
        Args:
            name: Zone name
            points: List of [x, y] coordinates
            color: RGB color tuple (default: auto-assigned)
            enabled: Whether zone is active
        """
        if color is None:
            color_idx = len(self.zones['zones']) % len(self.zone_colors)
            color = self.zone_colors[color_idx]
        
        new_zone = {
            'name': name,
            'points': points,
            'color': list(color),
            'enabled': enabled
        }
        
        self.zones['zones'].append(new_zone)
        print(f"Zone '{name}' created with {len(points)} points")


def main():
    """Main function to run zone manager"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Zone Manager - Create and edit detection zones')
    parser.add_argument('--source', type=str, default='Camera-Video.mp4',
                       help='Video source (0 for webcam, or path to video file)')
    parser.add_argument('--zones', type=str, default='zones.json',
                       help='Path to zones configuration file')
    parser.add_argument('--mode', type=str, default='interactive',
                       choices=['interactive', 'list'],
                       help='Mode: interactive editor or list zones')
    
    args = parser.parse_args()
    
    # Convert source to int if it's a number (camera index)
    try:
        video_source = int(args.source)
    except ValueError:
        video_source = args.source
    
    # Initialize zone manager
    manager = ZoneManager(zones_file=args.zones)
    
    if args.mode == 'interactive':
        manager.edit_zones_interactive(video_source=video_source)
    elif args.mode == 'list':
        manager.list_zones()


if __name__ == '__main__':
    main()
