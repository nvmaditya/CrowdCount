"""
Integrated People Detector with Dashboard Support
Extends the original detector to update shared state for the dashboard API.
This module does not break existing detection logic - it adds dashboard integration.
"""

import cv2
import numpy as np
from ultralytics import YOLO
from collections import defaultdict
import json
import os
from pathlib import Path
import sys
import threading
import time
from time import perf_counter
from urllib.request import urlopen
from urllib.error import URLError

from crowdcount.state import shared_state

try:
    from crowdcount.metrics import MetricsLogger
except Exception:  # pragma: no cover
    MetricsLogger = None  # type: ignore


class IntegratedPeopleDetector:
    """
    People detector with dashboard integration.
    Updates shared state after each frame for real-time dashboard updates.
    
    DETECTION QUALITY NOTES:
    - Uses yolov8m (medium) model for better accuracy than nano
    - Filters detections by confidence threshold (default 0.5)
    - Filters out very small bounding boxes (likely false positives)
    - Uses BoT-SORT tracker for more stable tracking IDs
    """
    
    # Detection quality parameters - tune these if count is still buggy
    DEFAULT_CONFIDENCE_THRESHOLD = 0.5  # Minimum confidence to count a detection
    DEFAULT_MIN_BOX_AREA = 1500  # Minimum bounding box area in pixels (filters tiny detections)
    DEFAULT_IOU_THRESHOLD = 0.5  # IoU threshold for NMS (lower = fewer overlapping detections)
    
    def __init__(self, model_path='yolov8m.pt', zones_file='zones.json',
                 conf_threshold=None, min_box_area=None, iou_threshold=None):
        """
        Initialize the people detector with YOLOv8
        
        Args:
            model_path: Path to YOLOv8 model weights
                       Recommended: 'yolov8m.pt' (medium) or 'yolov8l.pt' (large) for accuracy
                       Use 'yolov8n.pt' (nano) or 'yolov8s.pt' (small) for speed
            zones_file: Path to zones configuration JSON file
            conf_threshold: Minimum confidence score (0.0-1.0) to accept detection
            min_box_area: Minimum bounding box area in pixels
            iou_threshold: IoU threshold for non-maximum suppression
        """
        self.model = YOLO(model_path)
        self.zones_file = zones_file
        self.zones = self.load_zones()
        
        # Detection quality parameters
        self.conf_threshold = conf_threshold or self.DEFAULT_CONFIDENCE_THRESHOLD
        self.min_box_area = min_box_area or self.DEFAULT_MIN_BOX_AREA
        self.iou_threshold = iou_threshold or self.DEFAULT_IOU_THRESHOLD
        
        # Track history for smooth tracking
        self.track_history = defaultdict(lambda: [])
        
        # Zone statistics: track unique IDs that entered each zone
        self.zone_visitors = defaultdict(set)  # {zone_name: set(track_ids)}
        self.zone_current_count = defaultdict(int)  # {zone_name: current_count}
        
        # Track ID stabilization - remember recent IDs to handle brief occlusions
        self.recent_track_ids = {}  # {track_id: last_seen_frame}
        self.frame_counter = 0
        self.id_memory_frames = 30  # Remember IDs for this many frames
        
        # Interactive zone drawing
        self.drawing_mode = False
        self.current_zone_points = []
        
        # Colors for visualization
        self.colors = self.generate_colors(100)
        
        # Frame dimensions (will be set on first frame)
        self.frame_width = 0
        self.frame_height = 0

        # Optional metrics logging (enabled via process_video args)
        self._metrics_logger = None
        self._last_infer_ms = 0.0
        self._last_post_ms = 0.0

    def _is_dashboard_reachable(self, dashboard_url):
        """Check if dashboard endpoint is reachable before advertising it as available."""
        try:
            with urlopen(dashboard_url, timeout=1.0) as response:
                return 200 <= response.status < 400
        except (URLError, TimeoutError, ValueError):
            return False
        
    def generate_colors(self, n):
        """Generate n distinct colors for visualization"""
        colors = []
        for i in range(n):
            hue = int(180 * i / n)
            col = cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0][0]
            colors.append((int(col[0]), int(col[1]), int(col[2])))
        return colors
    
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
    
    def draw_zones(self, frame):
        """Draw all defined zones on the frame with statistics"""
        overlay = frame.copy()
        
        for zone in self.zones.get('zones', []):
            if not zone.get('enabled', True):
                continue
                
            points = np.array(zone['points'], dtype=np.int32)
            color = tuple(zone.get('color', [0, 255, 0]))
            zone_name = zone.get('name', 'Unnamed')
            
            # Draw filled polygon with transparency
            cv2.fillPoly(overlay, [points], color)
            
            # Draw polygon border
            cv2.polylines(frame, [points], True, color, 2)
            
            # Draw zone statistics
            centroid = points.mean(axis=0).astype(int)
            current = self.zone_current_count.get(zone_name, 0)
            total = len(self.zone_visitors.get(zone_name, set()))
            
            # Zone name
            cv2.putText(frame, zone_name, (centroid[0], centroid[1] - 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            # Statistics: Current count / Total unique
            stats_text = f"Current: {current} | Total: {total}"
            cv2.putText(frame, stats_text, (centroid[0], centroid[1] + 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        
        # Blend overlay with frame for transparency effect
        cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)
        return frame
    
    def point_in_zone(self, point, zone_points):
        """Check if a point is inside a polygon zone"""
        return cv2.pointPolygonTest(np.array(zone_points, dtype=np.int32), point, False) >= 0
    
    def get_person_zone(self, bbox_center):
        """Determine which zone(s) a person is in"""
        zones_detected = []
        for zone in self.zones.get('zones', []):
            if not zone.get('enabled', True):
                continue
            if self.point_in_zone(bbox_center, zone['points']):
                zones_detected.append(zone.get('name', 'Unnamed'))
        return zones_detected
    
    def update_zone_statistics(self, detections):
        """Update zone visitor counts and statistics"""
        # Reset current counts
        self.zone_current_count.clear()
        
        # Count current people in each zone and track unique visitors
        for det in detections:
            track_id = det['id']
            zones = det['zones']
            
            for zone_name in zones:
                # Increment current count
                self.zone_current_count[zone_name] = self.zone_current_count.get(zone_name, 0) + 1
                
                # Add to unique visitors set
                self.zone_visitors[zone_name].add(track_id)
    
    def update_shared_state(self, detections):
        """
        Update the shared state for dashboard integration.
        Called after each frame processing.
        """
        # Collect person coordinates for heatmap
        coordinates = [(det['center'][0], det['center'][1]) for det in detections]
        
        # Update shared state atomically
        shared_state.update_counts(
            total_count=len(detections),
            zone_counts=dict(self.zone_current_count),
            zone_visitors=dict(self.zone_visitors),
            coordinates=coordinates
        )
    
    def print_zone_statistics(self):
        """Print detailed zone statistics"""
        print("\n=== Zone Statistics ===")
        for zone in self.zones.get('zones', []):
            if not zone.get('enabled', True):
                continue
            zone_name = zone.get('name', 'Unnamed')
            current = self.zone_current_count.get(zone_name, 0)
            total = len(self.zone_visitors.get(zone_name, set()))
            print(f"{zone_name}:")
            print(f"  Current Count: {current}")
            print(f"  Total Visitors: {total}")
            if total > 0:
                visitor_ids = sorted(list(self.zone_visitors[zone_name]))
                print(f"  Visitor IDs: {visitor_ids}")
        print("=====================\n")
    
    def mouse_callback(self, event, x, y, flags, param):
        """Handle mouse events for interactive zone drawing"""
        if not self.drawing_mode:
            return
            
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
        print("\nEnter zone name (or press Enter for default): ", end='', flush=True)
        zone_name = f"Zone_{len(self.zones['zones']) + 1}"
        
        # Get color for this zone
        zone_colors = [(0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255)]
        color_idx = len(self.zones['zones']) % len(zone_colors)
        color = zone_colors[color_idx]
        
        # Create zone dictionary
        new_zone = {
            'name': zone_name,
            'points': self.current_zone_points,
            'color': list(color),
            'enabled': True
        }
        
        self.zones['zones'].append(new_zone)
        print(f"\nZone '{zone_name}' created with {len(self.current_zone_points)} points")
        
        # Reset current zone
        self.current_zone_points = []
    
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
        
        return frame
    
    def detect_people(self, frame):
        """
        Detect people in frame using YOLOv8 with tracking
        
        Args:
            frame: Input video frame
            
        Returns:
            annotated_frame: Frame with detections drawn
            detections: List of detection dictionaries
            
        Quality improvements:
        - Filters by confidence threshold
        - Filters by minimum bounding box size
        - Uses BoT-SORT tracker with optimized parameters
        """
        self.frame_counter += 1
        
        # Run YOLOv8 tracking with optimized parameters
        # Using BoT-SORT tracker for better ID stability
        infer_start = perf_counter()
        results = self.model.track(
            frame, 
            persist=True, 
            classes=[0],  # Only detect people (class 0)
            conf=self.conf_threshold,  # Confidence threshold
            iou=self.iou_threshold,  # IoU threshold for NMS
            tracker="botsort.yaml",  # Better tracker than default ByteTrack
            verbose=False
        )
        infer_end = perf_counter()
        self._last_infer_ms = (infer_end - infer_start) * 1000.0
        
        detections = []
        annotated_frame = frame.copy()
        
        # Draw zones first (background layer)
        annotated_frame = self.draw_zones(annotated_frame)
        
        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.cpu().numpy().astype(int)
            confidences = results[0].boxes.conf.cpu().numpy()
            
            for box, track_id, conf in zip(boxes, track_ids, confidences):
                x1, y1, x2, y2 = box.astype(int)
                
                # FILTER 1: Skip low confidence detections
                # (should be handled by model.track conf param, but double-check)
                if conf < self.conf_threshold:
                    continue
                
                # FILTER 2: Skip very small bounding boxes (likely false positives)
                box_width = x2 - x1
                box_height = y2 - y1
                box_area = box_width * box_height
                if box_area < self.min_box_area:
                    continue
                
                # FILTER 3: Skip boxes with unrealistic aspect ratios
                # People are typically taller than wide (aspect ratio > 0.3)
                aspect_ratio = box_width / max(box_height, 1)
                if aspect_ratio > 2.0 or aspect_ratio < 0.15:
                    continue
                
                # Calculate center point
                center_x = int((x1 + x2) / 2)
                center_y = int((y1 + y2) / 2)
                center = (center_x, center_y)
                
                # Update track ID memory
                self.recent_track_ids[track_id] = self.frame_counter
                
                # Check which zone(s) person is in
                zones = self.get_person_zone(center)
                
                # Store detection info
                detection = {
                    'id': int(track_id),
                    'bbox': [x1, y1, x2, y2],
                    'center': center,
                    'confidence': float(conf),
                    'zones': zones
                }
                detections.append(detection)
                
                # Get color for this ID
                color = self.colors[track_id % len(self.colors)]
                
                # Draw bounding box
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                
                # Draw ID and confidence
                label = f'ID:{track_id} ({conf:.2f})'
                label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                
                # Draw label background
                cv2.rectangle(annotated_frame,
                            (x1, y1 - label_size[1] - 10),
                            (x1 + label_size[0], y1),
                            color, -1)
                
                # Draw label text
                cv2.putText(annotated_frame, label, (x1, y1 - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                
                # Draw zone information
                if zones:
                    zone_text = f"Zone: {', '.join(zones)}"
                    cv2.putText(annotated_frame, zone_text, (x1, y2 + 20),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                
                # Draw center point
                cv2.circle(annotated_frame, center, 4, color, -1)
                
                # Update track history
                self.track_history[track_id].append(center)
                if len(self.track_history[track_id]) > 30:
                    self.track_history[track_id].pop(0)
                
                # Draw tracking trail
                points = np.array(self.track_history[track_id], dtype=np.int32)
                cv2.polylines(annotated_frame, [points], False, color, 2)
        
        # Update zone statistics after all detections
        self.update_zone_statistics(detections)
        
        # Update shared state for dashboard
        self.update_shared_state(detections)

        # Post-processing time (everything after inference in this method)
        post_end = perf_counter()
        self._last_post_ms = (post_end - infer_end) * 1000.0

        # Metrics: use SharedState's per-frame timestamp (set during update_shared_state)
        if self._metrics_logger is not None:
            ts = (
                shared_state.get_last_update().isoformat()
                if shared_state.get_last_update()
                else time.strftime("%Y-%m-%dT%H:%M:%S")
            )
            for det in detections:
                track_id = int(det["id"])
                self._metrics_logger.log_detection(
                    timestamp=ts,
                    frame_index=self.frame_counter,
                    track_id=track_id,
                    confidence=float(det["confidence"]),
                    zones=list(det["zones"]),
                    bbox=list(det["bbox"]),
                    center=det["center"],
                )
                self._metrics_logger.note_track_seen(
                    timestamp=ts,
                    frame_index=self.frame_counter,
                    track_id=track_id,
                    confidence=float(det["confidence"]),
                    trail_len=len(self.track_history[track_id]),
                )
        
        # Draw current zone being created (if in drawing mode)
        if self.drawing_mode:
            annotated_frame = self.draw_current_zone(annotated_frame)
        
        return annotated_frame, detections
    
    def process_video(
        self,
        video_source=0,
        output_path=None,
        display=True,
        speed=1.0,
        metrics_out_dir=None,
        max_frames=None,
        history_dump_limit=300,
    ):
        """
        Process video from source (file or camera)
        
        Args:
            video_source: Video file path or camera index (0 for webcam)
            output_path: Optional path to save output video
            display: Whether to display the video
            speed: Playback speed multiplier (e.g., 2.0 for 2x speed)
                   Values > 1.0 skip frames to speed up processing
        """
        cap = cv2.VideoCapture(video_source)
        
        if not cap.isOpened():
            print(f"Error: Cannot open video source {video_source}")
            return
        
        # Get video properties
        self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(cap.get(cv2.CAP_PROP_FPS)) or 30
        
        # Calculate frame skip for speed control
        # speed=2.0 means process every 2nd frame
        frame_skip = max(1, int(speed))
        
        # Set frame dimensions in shared state for heatmap
        shared_state.set_frame_dimensions(self.frame_width, self.frame_height)
        shared_state.set_detection_running(True)
        
        # Setup video writer if output path specified
        writer = None
        if output_path:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (self.frame_width, self.frame_height))
        
        print(f"Processing video: {video_source}")
        print(f"Resolution: {self.frame_width}x{self.frame_height} @ {fps} FPS")
        print(f"Speed: {speed}x (processing every {frame_skip} frame(s))")
        print("\n=== Controls ===")
        print("Press 'q' to quit")
        print("Press 's' to save zones")
        print("Press 'd' to toggle drawing mode")
        print("  - Left Click: Add point to zone")
        print("  - Right Click: Finish zone")
        print("  - Middle Click: Remove last point")
        print("Press 'c' to clear current zone")
        print("Press 'z' to print zone statistics")
        print("Press '+' to increase speed")
        print("Press '-' to decrease speed")
        print("================\n")
        dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:8080/static/index.html")
        if self._is_dashboard_reachable(dashboard_url):
            print(f"Dashboard available at: {dashboard_url}")
        else:
            print(f"Dashboard server not detected at: {dashboard_url}")
            print("Start it with: python run_app.py --server-only")
        
        window_name = 'YOLOv8 People Detection (Dashboard Enabled)'
        
        if display:
            cv2.namedWindow(window_name)
            cv2.setMouseCallback(window_name, self.mouse_callback)
        
        frame_count = 0
        frames_read = 0

        # Metrics logging (optional)
        if metrics_out_dir is not None:
            if MetricsLogger is None:
                print("Warning: metrics logging requested but MetricsLogger import failed")
            else:
                zone_names = [
                    z.get("name", "Unnamed")
                    for z in self.zones.get("zones", [])
                    if z.get("enabled", True)
                ]
                try:
                    self._metrics_logger = MetricsLogger(Path(metrics_out_dir), zone_names)
                    print(f"Metrics logging enabled -> {metrics_out_dir}")
                except Exception as exc:
                    print(f"Warning: failed to initialize metrics logging: {exc}")
                    self._metrics_logger = None
        
        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    if display:
                        # Loop video for continuous demo
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    # Headless runs should terminate cleanly at EOF
                    break
                
                frames_read += 1
                
                # Skip frames for speed control
                if frames_read % frame_skip != 0:
                    continue
                
                frame_count += 1

                frame_start = perf_counter()
                
                # Detect people
                annotated_frame, detections = self.detect_people(frame)

                frame_end = perf_counter()
                frame_ms = (frame_end - frame_start) * 1000.0
                fps_inst = (1000.0 / frame_ms) if frame_ms > 0 else 0.0

                if self._metrics_logger is not None:
                    ts = shared_state.get_last_update().isoformat() if shared_state.get_last_update() else time.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    self._metrics_logger.log_frame_timing(
                        timestamp=ts,
                        frame_index=self.frame_counter,
                        frames_read=frames_read,
                        source_fps=int(fps),
                        frame_width=int(self.frame_width),
                        frame_height=int(self.frame_height),
                        speed_frame_skip=int(frame_skip),
                        people_count=len(detections),
                        infer_ms=float(self._last_infer_ms),
                        post_ms=float(self._last_post_ms),
                        frame_ms=float(frame_ms),
                        fps_inst=float(fps_inst),
                    )
                    self._metrics_logger.log_zone_counts(
                        timestamp=ts,
                        frame_index=self.frame_counter,
                        total_count=len(detections),
                        zone_counts=dict(self.zone_current_count),
                    )
                
                # Add frame info
                info_text = f"Frame: {frame_count} | People: {len(detections)} | Speed: {frame_skip}x"
                cv2.putText(annotated_frame, info_text, (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                # Add dashboard indicator
                cv2.putText(annotated_frame, "Dashboard: http://localhost:8000", (10, 60),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                
                # Add drawing mode indicator
                if self.drawing_mode:
                    mode_text = f"DRAWING MODE | Points: {len(self.current_zone_points)}"
                    cv2.putText(annotated_frame, mode_text, (10, 90),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                
                # Write frame to output
                if writer:
                    writer.write(annotated_frame)
                
                # Display frame
                if display:
                    cv2.imshow(window_name, annotated_frame)
                    
                    # Use waitKey(1) for fastest processing
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        print("Quitting...")
                        break
                    elif key == ord('s'):
                        self.save_zones()
                        print("Zones saved!")
                    elif key == ord('d'):
                        self.drawing_mode = not self.drawing_mode
                        mode = "ON" if self.drawing_mode else "OFF"
                        print(f"Drawing mode: {mode}")
                    elif key == ord('+') or key == ord('='):
                        frame_skip = min(frame_skip + 1, 10)
                        print(f"Speed increased to {frame_skip}x")
                    elif key == ord('-') or key == ord('_'):
                        frame_skip = max(frame_skip - 1, 1)
                        print(f"Speed decreased to {frame_skip}x")
                    elif key == ord('c'):
                        self.current_zone_points = []
                        print("Current zone cleared")
                    elif key == ord('z'):
                        self.print_zone_statistics()

                if max_frames is not None and frame_count >= int(max_frames):
                    print(f"Reached max_frames={max_frames}; stopping.")
                    break
        
        finally:
            shared_state.set_detection_running(False)
            cap.release()
            if writer:
                writer.release()
            if display:
                cv2.destroyAllWindows()

            # Flush metrics outputs at end of run
            if self._metrics_logger is not None:
                try:
                    self._metrics_logger.write_track_summaries()
                    history = shared_state.get_history(int(history_dump_limit))
                    self._metrics_logger.write_history_dump({"history": history, "count": len(history)})
                    self._metrics_logger.close()
                except Exception as exc:
                    print(f"Warning: failed to finalize metrics outputs: {exc}")
            
            print(f"\nProcessing complete. Total frames: {frame_count}")


def run_detector(video_source, model_path, zones_file, output_path, display):
    """Run the detector in a separate thread"""
    detector = IntegratedPeopleDetector(model_path=model_path, zones_file=zones_file)
    detector.process_video(
        video_source=video_source,
        output_path=output_path,
        display=display
    )


def main():
    """Main function to run people detection with dashboard"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='YOLOv8 People Detection with Dashboard',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Model Options (accuracy vs speed):
  yolov8n.pt  - Nano (fastest, least accurate) - NOT recommended for counting
  yolov8s.pt  - Small (fast, moderate accuracy)
  yolov8m.pt  - Medium (balanced) - RECOMMENDED default
  yolov8l.pt  - Large (slower, more accurate)
  yolov8x.pt  - Extra Large (slowest, most accurate)

If count is still buggy, try:
  --conf 0.6        Increase confidence threshold (default 0.5)
  --min-area 2000   Increase minimum box area (default 1500)
  --model yolov8l.pt  Use a larger model
        """
    )
    parser.add_argument('--source', type=str, default='Camera-Video.mp4',
                       help='Video source (0 for webcam, or path to video file)')
    parser.add_argument('--model', type=str, default='yolov8m.pt',
                       help='YOLOv8 model path (default: yolov8m.pt for better accuracy)')
    parser.add_argument('--zones', type=str, default='zones.json',
                       help='Path to zones configuration file')
    parser.add_argument('--output', type=str, default=None,
                       help='Path to save output video')
    parser.add_argument('--no-display', action='store_true',
                       help='Disable video display')
    parser.add_argument('--conf', type=float, default=0.5,
                       help='Confidence threshold (0.0-1.0, higher = fewer false positives)')
    parser.add_argument('--min-area', type=int, default=1500,
                       help='Minimum bounding box area in pixels')
    parser.add_argument('--iou', type=float, default=0.5,
                       help='IoU threshold for NMS (lower = fewer overlapping detections)')
    
    args = parser.parse_args()
    
    # Convert source to int if it's a number (camera index)
    try:
        video_source = int(args.source)
    except ValueError:
        video_source = args.source
    
    print(f"\n=== Detection Configuration ===")
    print(f"Model: {args.model}")
    print(f"Confidence threshold: {args.conf}")
    print(f"Min box area: {args.min_area} px")
    print(f"IoU threshold: {args.iou}")
    print(f"================================\n")
    
    # Initialize detector with custom parameters
    detector = IntegratedPeopleDetector(
        model_path=args.model, 
        zones_file=args.zones,
        conf_threshold=args.conf,
        min_box_area=args.min_area,
        iou_threshold=args.iou
    )
    
    # Process video
    detector.process_video(
        video_source=video_source,
        output_path=args.output,
        display=not args.no_display
    )


if __name__ == '__main__':
    main()
