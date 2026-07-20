import ARKit
import CoreLocation
import Foundation
import RealityKit
import SwiftUI
import simd

final class ARKitObservationProvider: NSObject, ObservableObject, ARSessionDelegate, CLLocationManagerDelegate {
    @Published private(set) var isRunning = false
    @Published private(set) var trackingStatus = "unavailable"
    @Published private(set) var worldMappingStatus = "notAvailable"
    @Published private(set) var planeCount = 0
    @Published private(set) var depthAvailable = false
    @Published private(set) var lidarAvailable = false
    @Published private(set) var latestObservation: DynamicJSON?
    @Published private(set) var sequence = 0

    private weak var session: ARSession?
    private let locationManager = CLLocationManager()
    private var latestLocation: CLLocation?
    private var lastObservationAt: TimeInterval = 0
    private let observationInterval: TimeInterval = 2.0

    override init() {
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        locationManager.distanceFilter = 10
    }

    func attach(to session: ARSession) {
        self.session = session
        session.delegate = self
        session.delegateQueue = .main
    }

    func start() {
        guard ARWorldTrackingConfiguration.isSupported, let session else {
            trackingStatus = "unavailable"
            return
        }
        let configuration = ARWorldTrackingConfiguration()
        configuration.planeDetection = [.horizontal, .vertical]
        configuration.environmentTexturing = .automatic
        lidarAvailable = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
        if lidarAvailable {
            configuration.sceneReconstruction = .meshWithClassification
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            configuration.frameSemantics.insert(.sceneDepth)
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            configuration.frameSemantics.insert(.smoothedSceneDepth)
        }
        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        isRunning = true
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
    }

    func stop() {
        session?.pause()
        locationManager.stopUpdatingLocation()
        isRunning = false
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        trackingStatus = Self.trackingLabel(frame.camera.trackingState)
        worldMappingStatus = Self.mappingLabel(frame.worldMappingStatus)
        depthAvailable = frame.sceneDepth != nil || frame.smoothedSceneDepth != nil
        let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
        planeCount = planes.count
        guard isRunning, frame.timestamp - lastObservationAt >= observationInterval else { return }
        lastObservationAt = frame.timestamp
        sequence += 1
        latestObservation = buildObservation(frame: frame, planes: planes)
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        trackingStatus = "unavailable"
        isRunning = false
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        latestLocation = locations.last
    }

    private func buildObservation(frame: ARFrame, planes: [ARPlaneAnchor]) -> DynamicJSON {
        let transform = frame.camera.transform
        let rotation = simd_quatf(transform)
        var payload: [String: DynamicJSON] = [
            "provider_type": .string("arkit"),
            "session_id": .string("arkit-foreground"),
            "sequence": .number(Double(sequence)),
            "observed_at": .string(ISO8601DateFormatter().string(from: Date())),
            "tracking": .object([
                "status": .string(trackingStatus),
                "world_mapping_status": .string(worldMappingStatus)
            ]),
            "camera_pose": .object([
                "position": .object([
                    "x": .number(Double(transform.columns.3.x)),
                    "y": .number(Double(transform.columns.3.y)),
                    "z": .number(Double(transform.columns.3.z))
                ]),
                "orientation": .object([
                    "x": .number(Double(rotation.vector.x)),
                    "y": .number(Double(rotation.vector.y)),
                    "z": .number(Double(rotation.vector.z)),
                    "w": .number(Double(rotation.vector.w))
                ])
            ]),
            "depth": .object([
                "available": .bool(depthAvailable),
                "confidence": .number(depthAvailable ? 1 : 0)
            ]),
            "objects": .array([]),
            "planes": .array(planes.prefix(128).map(Self.planeObservation)),
            "relations": .array([]),
            "environment": .object([
                "rgb_tracking_available": .bool(true),
                "imu_fusion_active": .bool(true),
                "lidar_available": .bool(lidarAvailable),
                "ambient_intensity_lumens": .number(Double(frame.lightEstimate?.ambientIntensity ?? 0))
            ]),
            "confidence": .number(trackingStatus == "normal" ? 1 : 0.5)
        ]
        if let location = latestLocation, location.horizontalAccuracy >= 0 {
            let latitude = (location.coordinate.latitude * 10_000).rounded() / 10_000
            let longitude = (location.coordinate.longitude * 10_000).rounded() / 10_000
            payload["location"] = .object([
                "latitude": .number(latitude),
                "longitude": .number(longitude),
                "altitude_m": .number(location.altitude),
                "horizontal_accuracy_m": .number(location.horizontalAccuracy)
            ])
        }
        return .object(payload)
    }

    private static func planeObservation(_ plane: ARPlaneAnchor) -> DynamicJSON {
        let worldCenter = plane.transform * SIMD4<Float>(plane.center.x, plane.center.y, plane.center.z, 1)
        return .object([
            "anchor_id": .string(plane.identifier.uuidString.lowercased()),
            "kind": .string("plane"),
            "classification": .string(String(describing: plane.classification)),
            "alignment": .string(plane.alignment == .horizontal ? "horizontal" : "vertical"),
            "center": .object([
                "x": .number(Double(worldCenter.x)),
                "y": .number(Double(worldCenter.y)),
                "z": .number(Double(worldCenter.z))
            ]),
            "extent": .object([
                "x": .number(Double(plane.extent.x)),
                "y": .number(Double(plane.extent.y)),
                "z": .number(Double(plane.extent.z))
            ]),
            "confidence": .number(1)
        ])
    }

    private static func trackingLabel(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal:
            return "normal"
        case .limited:
            return "limited"
        case .notAvailable:
            return "unavailable"
        }
    }

    private static func mappingLabel(_ status: ARFrame.WorldMappingStatus) -> String {
        switch status {
        case .notAvailable: return "notAvailable"
        case .limited: return "limited"
        case .extending: return "extending"
        case .mapped: return "mapped"
        @unknown default: return "unknown"
        }
    }
}

struct RealityKitObservationView: UIViewRepresentable {
    @ObservedObject var provider: ARKitObservationProvider

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        provider.attach(to: view.session)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
