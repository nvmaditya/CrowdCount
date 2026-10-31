"""
Run Application - Starts both the API server and detector together
This script manages both processes for easy startup.
"""

import sys
import os
import threading
import time
import argparse
from pathlib import Path


def run_api_server(host='0.0.0.0', port=8080):
    """Run the FastAPI server"""
    import uvicorn
    from crowdcount.server.app import app
    
    print(f"\n{'='*50}")
    print(f"Starting API Server on http://{host}:{port}")
    print(f"Dashboard: http://localhost:{port}/static/index.html")
    print(f"{'='*50}\n")
    
    # Configure uvicorn to be less verbose
    uvicorn.run(
        app, 
        host=host, 
        port=port, 
        log_level="warning"
    )


def run_detector(video_source, model_path, zones_file, display,
                 conf_threshold=0.5, min_box_area=1500, iou_threshold=0.5, speed=1.0,
                 dashboard_port=8080, metrics_out_dir=None, max_frames=None):
    """Run the people detector"""
    from crowdcount.detector import IntegratedPeopleDetector
    
    print(f"\n{'='*50}")
    print(f"Starting People Detection")
    print(f"Video source: {video_source}")
    print(f"Model: {model_path}")
    print(f"Confidence: {conf_threshold} | Min Area: {min_box_area}")
    print(f"Speed: {speed}x")
    print(f"{'='*50}\n")

    # Share dashboard URL with detector so it prints the correct endpoint.
    os.environ["DASHBOARD_URL"] = f"http://localhost:{dashboard_port}/static/index.html"
    
    # Small delay to let server start
    time.sleep(2)
    
    detector = IntegratedPeopleDetector(
        model_path=model_path, 
        zones_file=zones_file,
        conf_threshold=conf_threshold,
        min_box_area=min_box_area,
        iou_threshold=iou_threshold
    )
    detector.process_video(
        video_source=video_source,
        output_path=None,
        display=display,
        speed=speed,
        metrics_out_dir=metrics_out_dir,
        max_frames=max_frames,
    )


def main():
    """Main entry point - runs both server and detector"""
    parser = argparse.ArgumentParser(
        description='People Detection Dashboard - Run API and Detector together',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_app.py                           # Run with defaults
  python run_app.py --source 0                # Use webcam
  python run_app.py --source video.mp4        # Use video file
  python run_app.py --port 8080               # Use different port
  python run_app.py --detector-only           # Run only detector
  python run_app.py --server-only             # Run only server
        """
    )
    
    parser.add_argument('--source', type=str, default='Camera-Video.mp4',
                       help='Video source (0 for webcam, or path to video file)')
    parser.add_argument('--model', type=str, default='yolov8m.pt',
                       help='YOLOv8 model path (use yolov8m.pt or larger for accuracy)')
    parser.add_argument('--zones', type=str, default='zones.json',
                       help='Path to zones configuration file')
    parser.add_argument('--host', type=str, default='0.0.0.0',
                       help='API server host')
    parser.add_argument('--port', type=int, default=8080,
                       help='API server port')
    parser.add_argument('--no-display', action='store_true',
                       help='Disable video display window')
    parser.add_argument('--server-only', action='store_true',
                       help='Run only the API server')
    parser.add_argument('--detector-only', action='store_true',
                       help='Run only the detector')
    
    # Detection quality parameters
    parser.add_argument('--conf', type=float, default=0.5,
                       help='Confidence threshold 0.0-1.0 (higher = fewer false positives)')
    parser.add_argument('--min-area', type=int, default=1500,
                       help='Minimum bounding box area in pixels')
    parser.add_argument('--iou', type=float, default=0.5,
                       help='IoU threshold for NMS')
    
    # Speed control
    parser.add_argument('--speed', type=float, default=1.0,
                       help='Video playback speed multiplier (e.g., 2.0 for 2x speed)')

    # Metrics logging (for paper plots)
    parser.add_argument('--metrics-out', type=str, default=None,
                       help='Directory to write run metrics CSV/JSON (confidence, FPS, zones, track lifetimes)')
    parser.add_argument('--max-frames', type=int, default=None,
                       help='Stop after this many processed frames (after frame skipping)')
    
    args = parser.parse_args()
    
    # Convert source to int if it's a number (camera index)
    try:
        video_source = int(args.source)
    except ValueError:
        video_source = args.source
    
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║           PEOPLE DETECTION DASHBOARD                         ║
    ║                                                              ║
    ║  This application runs:                                      ║
    ║  1. FastAPI backend server (for dashboard data)              ║
    ║  2. People detection with YOLOv8 (updates shared state)      ║
    ║                                                              ║
    ║  Dashboard URL: http://localhost:{port}/static/index.html    ║
    ╚══════════════════════════════════════════════════════════════╝
    """.format(port=args.port))
    
    if args.server_only:
        # Run only the server
        run_api_server(host=args.host, port=args.port)
    elif args.detector_only:
        # Run only the detector
        run_detector(video_source, args.model, args.zones, not args.no_display,
                     args.conf, args.min_area, args.iou, args.speed,
                     8080, args.metrics_out, args.max_frames)
    else:
        # Run both - server in background thread, detector in main thread
        server_thread = threading.Thread(
            target=run_api_server,
            args=(args.host, args.port),
            daemon=True
        )
        server_thread.start()
        
        # Run detector in main thread (needs to handle OpenCV window)
        try:
            run_detector(video_source, args.model, args.zones, not args.no_display,
                         args.conf, args.min_area, args.iou, args.speed, args.port,
                         args.metrics_out, args.max_frames)
        except KeyboardInterrupt:
            print("\nShutting down...")
        
        print("Application stopped.")


if __name__ == '__main__':
    main()
