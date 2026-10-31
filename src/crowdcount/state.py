"""
Shared State Module
Maintains synchronized state between the detection script and the backend API.
Thread-safe implementation using locks for concurrent access.
"""

import threading
from collections import deque
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
import numpy as np
import cv2


class SharedState:
    """
    Thread-safe shared state between detector and API.
    Singleton pattern to ensure single state across the application.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self._state_lock = threading.RLock()
        
        # Core counts
        self._total_count = 0
        self._zone_counts: Dict[str, int] = {}
        self._zone_visitors: Dict[str, set] = {}  # Unique visitors per zone
        
        # Person coordinates for heatmap (list of (x, y) tuples)
        self._person_coordinates: List[Tuple[int, int]] = []
        self._heatmap_accumulator: Optional[np.ndarray] = None
        self._frame_dimensions: Tuple[int, int] = (1920, 1080)  # Default, updated by detector
        
        # History for charts (timestamp, total_count, zone_counts)
        self._history: deque = deque(maxlen=3600)  # Keep last hour of data at 1 sample/sec
        
        # Alert configuration
        self._global_threshold = 50  # Default global threshold
        self._zone_thresholds: Dict[str, int] = {}  # Per-zone thresholds

        # Track which alerts are currently active (to avoid repeated persistence)
        self._active_alert_keys: Set[str] = set()
        
        # Last update timestamp
        self._last_update: Optional[datetime] = None
        
        # Detection running status
        self._detection_running = False
        
    def update_counts(self, total_count: int, zone_counts: Dict[str, int], 
                      zone_visitors: Dict[str, set], coordinates: List[Tuple[int, int]]):
        """
        Update all counts atomically.
        Called by the detector after each frame.
        """
        with self._state_lock:
            self._total_count = total_count
            self._zone_counts = zone_counts.copy()
            self._zone_visitors = {k: v.copy() for k, v in zone_visitors.items()}
            self._person_coordinates = coordinates.copy()
            self._last_update = datetime.now()
            
            # Update heatmap accumulator
            self._update_heatmap(coordinates)
            
            # Record history (sample every update, deque handles max length)
            history_entry = {
                'timestamp': self._last_update.isoformat(),
                'total_count': total_count,
                'zone_counts': zone_counts.copy()
            }
            self._history.append(history_entry)
    
    def _update_heatmap(self, coordinates: List[Tuple[int, int]]):
        """Update the heatmap accumulator with new coordinates"""
        if self._heatmap_accumulator is None:
            h, w = self._frame_dimensions
            self._heatmap_accumulator = np.zeros((h, w), dtype=np.float32)
        
        # Add Gaussian blobs at each person's position
        for x, y in coordinates:
            if 0 <= x < self._frame_dimensions[1] and 0 <= y < self._frame_dimensions[0]:
                # Simple accumulation (Gaussian applied during retrieval)
                cv2.circle(self._heatmap_accumulator, (x, y), 30, 1, -1)
    
    def set_frame_dimensions(self, width: int, height: int):
        """Set frame dimensions for heatmap generation"""
        with self._state_lock:
            self._frame_dimensions = (height, width)
            # Reset heatmap accumulator with new dimensions
            self._heatmap_accumulator = np.zeros((height, width), dtype=np.float32)
    
    def get_total_count(self) -> int:
        """Get current total people count"""
        with self._state_lock:
            return self._total_count
    
    def get_zone_counts(self) -> Dict[str, dict]:
        """Get current zone counts with additional stats"""
        with self._state_lock:
            result = {}
            for zone_name, current_count in self._zone_counts.items():
                total_visitors = len(self._zone_visitors.get(zone_name, set()))
                result[zone_name] = {
                    'current': current_count,
                    'total_visitors': total_visitors
                }
            return result
    
    def get_history(self, limit: int = 300) -> List[dict]:
        """Get count history for charts (default last 5 minutes)"""
        with self._state_lock:
            history_list = list(self._history)
            return history_list[-limit:]
    
    def get_heatmap_image(self) -> Optional[bytes]:
        """Generate and return heatmap as PNG bytes"""
        with self._state_lock:
            if self._heatmap_accumulator is None:
                return None
            
            # Normalize accumulator
            heatmap = self._heatmap_accumulator.copy()
            if heatmap.max() > 0:
                heatmap = (heatmap / heatmap.max() * 255).astype(np.uint8)
            else:
                heatmap = heatmap.astype(np.uint8)
            
            # Apply Gaussian blur for smoothing
            heatmap = cv2.GaussianBlur(heatmap, (51, 51), 0)
            
            # Apply colormap
            heatmap_colored = cv2.applyColorMap(heatmap, cv2.COLORMAP_JET)
            
            # Encode as PNG
            _, buffer = cv2.imencode('.png', heatmap_colored)
            return buffer.tobytes()
    
    def get_coordinates(self) -> List[Tuple[int, int]]:
        """Get current person coordinates"""
        with self._state_lock:
            return self._person_coordinates.copy()
    
    def get_last_update(self) -> Optional[datetime]:
        """Get timestamp of last update"""
        with self._state_lock:
            return self._last_update
    
    # Alert configuration methods
    def set_global_threshold(self, threshold: int):
        """Set global crowd alert threshold"""
        with self._state_lock:
            self._global_threshold = threshold
    
    def get_global_threshold(self) -> int:
        """Get global crowd alert threshold"""
        with self._state_lock:
            return self._global_threshold
    
    def set_zone_threshold(self, zone_name: str, threshold: int):
        """Set threshold for a specific zone"""
        with self._state_lock:
            self._zone_thresholds[zone_name] = threshold
    
    def get_zone_threshold(self, zone_name: str) -> Optional[int]:
        """Get threshold for a specific zone"""
        with self._state_lock:
            return self._zone_thresholds.get(zone_name)
    
    def _compute_alerts_unlocked(self) -> Dict[str, dict]:
        """Compute alerts without acquiring the state lock (caller must hold it)."""
        alerts: Dict[str, dict] = {}

        # Check global threshold
        if self._total_count > self._global_threshold:
            alerts['global'] = {
                'type': 'global',
                'threshold': self._global_threshold,
                'current': self._total_count,
                'exceeded': True
            }

        # Check zone thresholds
        for zone_name, current in self._zone_counts.items():
            threshold = self._zone_thresholds.get(zone_name)
            if threshold and current > threshold:
                alerts[zone_name] = {
                    'type': 'zone',
                    'zone': zone_name,
                    'threshold': threshold,
                    'current': current,
                    'exceeded': True
                }

        return alerts

    def check_alerts(self) -> Dict[str, dict]:
        """Check all thresholds and return active alerts (no side effects)."""
        with self._state_lock:
            return self._compute_alerts_unlocked()

    def check_alerts_and_record(self) -> Dict[str, dict]:
        """Check thresholds and persist newly-triggered alerts (edge-triggered)."""
        to_record: List[Tuple[str, int, int]] = []
        with self._state_lock:
            alerts = self._compute_alerts_unlocked()
            current_keys = set(alerts.keys())

            newly_active = current_keys - self._active_alert_keys
            for alert_key in newly_active:
                alert = alerts.get(alert_key, {})
                threshold = alert.get('threshold')
                current = alert.get('current')
                if isinstance(threshold, int) and isinstance(current, int):
                    to_record.append((str(alert_key), threshold, current))

            # Update active keys for edge-triggering
            self._active_alert_keys = current_keys

        for alert_type, threshold, actual_count in to_record:
            try:
                from crowdcount.server.audit import record_alert
                record_alert(alert_type=alert_type, threshold=threshold, actual_count=actual_count)
            except Exception:
                # Do not crash detection/API if persistence fails
                continue

        return alerts
    
    def set_detection_running(self, running: bool):
        """Set detection running status"""
        with self._state_lock:
            self._detection_running = running
    
    def is_detection_running(self) -> bool:
        """Check if detection is running"""
        with self._state_lock:
            return self._detection_running
    
    def reset_heatmap(self):
        """Reset the heatmap accumulator"""
        with self._state_lock:
            if self._heatmap_accumulator is not None:
                self._heatmap_accumulator.fill(0)
    
    def clear_history(self):
        """Clear the history"""
        with self._state_lock:
            self._history.clear()
    
    def get_summary(self) -> dict:
        """Get a complete summary of current state"""
        with self._state_lock:
            return {
                'total_count': self._total_count,
                'zone_counts': self.get_zone_counts(),
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'detection_running': self._detection_running,
                'alerts': self.check_alerts(),
                'global_threshold': self._global_threshold,
                'zone_thresholds': self._zone_thresholds.copy()
            }


# Global instance for easy access
shared_state = SharedState()
