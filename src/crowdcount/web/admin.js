/**
 * Admin Panel JavaScript
 * Handles all admin panel functionality including API calls and UI updates
 */

const API_BASE = "";
let currentSection = "dashboard";
let authToken = null;

// ==================== Initialization ====================

document.addEventListener("DOMContentLoaded", function () {
    // Check authentication
    authToken = localStorage.getItem("auth_token");
    const userRole = localStorage.getItem("user_role");
    const username = localStorage.getItem("username");

    if (!authToken || userRole !== "admin") {
        window.location.href = "login.html";
        return;
    }

    // Display username
    document.getElementById(
        "admin-username"
    ).textContent = `Logged in as: ${username}`;

    // Setup navigation
    setupNavigation();

    // Load initial data
    loadDashboard();
});

function setupNavigation() {
    const navItems = document.querySelectorAll(".nav-item[data-section]");
    navItems.forEach((item) => {
        item.addEventListener("click", function () {
            const section = this.dataset.section;
            switchSection(section);
        });
    });
}

function switchSection(section) {
    // Update navigation
    document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.remove("active");
    });
    document
        .querySelector(`.nav-item[data-section="${section}"]`)
        .classList.add("active");

    // Update content
    document.querySelectorAll(".section").forEach((sec) => {
        sec.classList.remove("active");
    });
    document.getElementById(`${section}-section`).classList.add("active");

    // Update title
    const titles = {
        dashboard: "Dashboard Overview",
        users: "User Management",
        cameras: "Camera Management",
        zones: "Zone Management",
        thresholds: "Threshold Configuration",
        logs: "Activity Logs",
        alerts: "Alert History",
    };
    document.getElementById("section-title").textContent = titles[section];

    currentSection = section;

    // Load section data
    loadSectionData(section);
}

function loadSectionData(section) {
    switch (section) {
        case "dashboard":
            loadDashboard();
            break;
        case "users":
            loadUsers();
            break;
        case "cameras":
            loadCameras();
            break;
        case "zones":
            loadZones();
            break;
        case "thresholds":
            loadThresholds();
            break;
        case "logs":
            loadLogs();
            break;
        case "alerts":
            loadAlertHistory();
            break;
    }
}

function refreshCurrentSection() {
    loadSectionData(currentSection);
}

// ==================== API Helper ====================

async function apiCall(endpoint, method = "GET", body = null) {
    const headers = {
        Authorization: `Bearer ${authToken}`,
        "Content-Type": "application/json",
    };

    const options = {
        method,
        headers,
    };

    if (body) {
        options.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, options);

        if (response.status === 401) {
            // Unauthorized - redirect to login
            logout();
            return null;
        }

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || "Request failed");
        }

        return await response.json();
    } catch (error) {
        console.error("API call error:", error);
        showNotification("error", error.message);
        throw error;
    }
}

// ==================== Dashboard ====================

async function loadDashboard() {
    try {
        // Load stats
        const [users, cameras, zones, logs] = await Promise.all([
            apiCall("/admin/users"),
            apiCall("/admin/cameras"),
            apiCall("/admin/zones"),
            apiCall("/admin/logs?limit=10"),
        ]);

        document.getElementById("stat-users").textContent = users.length;
        document.getElementById("stat-cameras").textContent =
            cameras.cameras.length;
        document.getElementById("stat-zones").textContent = zones.zones.length;
        document.getElementById("stat-logs").textContent = logs.count;

        // Display recent activity
        displayRecentActivity(logs.logs);
    } catch (error) {
        console.error("Error loading dashboard:", error);
    }
}

function displayRecentActivity(logs) {
    const container = document.getElementById("recent-activity");

    if (logs.length === 0) {
        container.innerHTML =
            '<div class="text-center text-muted">No recent activity</div>';
        return;
    }

    container.innerHTML = logs
        .map(
            (log) => `
        <div class="log-entry ${log.category}">
            <div class="d-flex justify-content-between">
                <strong>${log.action.replace(/_/g, " ")}</strong>
                <small class="text-muted">${formatDateTime(
                    log.timestamp
                )}</small>
            </div>
            <div class="text-muted small">
                ${log.username || "System"} - ${log.details || "No details"}
            </div>
        </div>
    `
        )
        .join("");
}

// ==================== User Management ====================

async function loadUsers() {
    try {
        const users = await apiCall("/admin/users");
        displayUsers(users);
    } catch (error) {
        console.error("Error loading users:", error);
    }
}

function displayUsers(users) {
    const tbody = document.getElementById("users-table-body");

    if (users.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="5" class="text-center text-muted">No users found</td></tr>';
        return;
    }

    tbody.innerHTML = users
        .map(
            (user) => `
        <tr>
            <td>${user.username}</td>
            <td><span class="badge bg-${
                user.role === "admin" ? "danger" : "primary"
            } badge-role">${user.role}</span></td>
            <td>${formatDateTime(user.created_at)}</td>
            <td>${
                user.last_login ? formatDateTime(user.last_login) : "Never"
            }</td>
            <td class="table-actions">
                <button class="btn btn-sm btn-danger" onclick="deleteUser('${
                    user.id
                }', '${user.username}')" 
                        ${
                            user.id === localStorage.getItem("user_id")
                                ? 'disabled title="Cannot delete yourself"'
                                : ""
                        }>
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>
    `
        )
        .join("");
}

function showAddUserModal() {
    const modal = new bootstrap.Modal(document.getElementById("addUserModal"));
    document.getElementById("add-user-form").reset();
    modal.show();
}

async function addUser() {
    const username = document.getElementById("new-username").value;
    const password = document.getElementById("new-password").value;
    const role = document.getElementById("new-role").value;

    if (!username || !password) {
        showNotification("error", "Please fill in all fields");
        return;
    }

    try {
        await apiCall("/admin/users", "POST", { username, password, role });
        showNotification("success", "User added successfully");
        bootstrap.Modal.getInstance(
            document.getElementById("addUserModal")
        ).hide();
        loadUsers();
    } catch (error) {
        console.error("Error adding user:", error);
    }
}

async function deleteUser(userId, username) {
    if (!confirm(`Are you sure you want to delete user "${username}"?`)) {
        return;
    }

    try {
        await apiCall(`/admin/users/${userId}`, "DELETE");
        showNotification("success", "User deleted successfully");
        loadUsers();
    } catch (error) {
        console.error("Error deleting user:", error);
    }
}

// ==================== Camera Management ====================

async function loadCameras() {
    try {
        const data = await apiCall("/admin/cameras");
        displayCameras(data.cameras);
    } catch (error) {
        console.error("Error loading cameras:", error);
    }
}

function displayCameras(cameras) {
    const container = document.getElementById("cameras-container");

    if (cameras.length === 0) {
        container.innerHTML =
            '<div class="text-center text-muted">No cameras configured</div>';
        return;
    }

    container.innerHTML = cameras
        .map(
            (camera) => `
        <div class="camera-item">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <h6 class="mb-1">${camera.name}</h6>
                    <p class="text-muted mb-1"><small><i class="bi bi-link-45deg"></i> ${
                        camera.source_url
                    }</small></p>
                    <span class="badge bg-${
                        camera.enabled ? "success" : "secondary"
                    }">${camera.enabled ? "Enabled" : "Disabled"}</span>
                </div>
                <div>
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="toggleCamera('${
                        camera.id
                    }', ${!camera.enabled})">
                        <i class="bi bi-${
                            camera.enabled ? "pause" : "play"
                        }-circle"></i>
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="deleteCamera('${
                        camera.id
                    }', '${camera.name}')">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
        </div>
    `
        )
        .join("");
}

function showAddCameraModal() {
    const modal = new bootstrap.Modal(
        document.getElementById("addCameraModal")
    );
    document.getElementById("add-camera-form").reset();
    modal.show();
}

async function addCamera() {
    const name = document.getElementById("camera-name").value;
    const source_url = document.getElementById("camera-source").value;
    const enabled = document.getElementById("camera-enabled").checked;

    if (!name || !source_url) {
        showNotification("error", "Please fill in all fields");
        return;
    }

    try {
        await apiCall("/admin/cameras", "POST", { name, source_url, enabled });
        showNotification("success", "Camera added successfully");
        bootstrap.Modal.getInstance(
            document.getElementById("addCameraModal")
        ).hide();
        loadCameras();
    } catch (error) {
        console.error("Error adding camera:", error);
    }
}

async function toggleCamera(cameraId, enabled) {
    try {
        await apiCall(`/admin/cameras/${cameraId}`, "PUT", { enabled });
        showNotification(
            "success",
            `Camera ${enabled ? "enabled" : "disabled"}`
        );
        loadCameras();
    } catch (error) {
        console.error("Error toggling camera:", error);
    }
}

async function deleteCamera(cameraId, cameraName) {
    if (!confirm(`Are you sure you want to delete camera "${cameraName}"?`)) {
        return;
    }

    try {
        await apiCall(`/admin/cameras/${cameraId}`, "DELETE");
        showNotification("success", "Camera deleted successfully");
        loadCameras();
    } catch (error) {
        console.error("Error deleting camera:", error);
    }
}

// ==================== Zone Management ====================

async function loadZones() {
    try {
        const data = await apiCall("/admin/zones");
        displayZones(data.zones);
    } catch (error) {
        console.error("Error loading zones:", error);
    }
}

function displayZones(zones) {
    const container = document.getElementById("zones-container");

    if (zones.length === 0) {
        container.innerHTML =
            '<div class="text-center text-muted">No zones configured</div>';
        return;
    }

    container.innerHTML = zones
        .map(
            (zone) => `
        <div class="zone-item">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <h6 class="mb-1">${zone.name}</h6>
                    <p class="text-muted mb-1"><small><i class="bi bi-pentagon"></i> ${
                        zone.points.length
                    } points</small></p>
                    <div>
                        <span class="badge bg-${
                            zone.enabled ? "success" : "secondary"
                        }">${zone.enabled ? "Enabled" : "Disabled"}</span>
                        ${
                            zone.threshold
                                ? `<span class="badge bg-warning">Threshold: ${zone.threshold}</span>`
                                : ""
                        }
                    </div>
                </div>
                <div>
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="toggleZone('${
                        zone.name
                    }', ${!zone.enabled})">
                        <i class="bi bi-${
                            zone.enabled ? "pause" : "play"
                        }-circle"></i>
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="deleteZone('${
                        zone.name
                    }')">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
        </div>
    `
        )
        .join("");
}

function showAddZoneModal() {
    const modal = new bootstrap.Modal(document.getElementById("addZoneModal"));
    modal.show();
}

async function toggleZone(zoneName, enabled) {
    try {
        await apiCall(`/admin/zones/${zoneName}`, "PUT", { enabled });
        showNotification("success", `Zone ${enabled ? "enabled" : "disabled"}`);
        loadZones();
    } catch (error) {
        console.error("Error toggling zone:", error);
    }
}

async function deleteZone(zoneName) {
    if (!confirm(`Are you sure you want to delete zone "${zoneName}"?`)) {
        return;
    }

    try {
        await apiCall(`/admin/zones/${zoneName}`, "DELETE");
        showNotification("success", "Zone deleted successfully");
        loadZones();
    } catch (error) {
        console.error("Error deleting zone:", error);
    }
}

// ==================== Threshold Management ====================

async function loadThresholds() {
    try {
        const [thresholds, zones] = await Promise.all([
            apiCall("/admin/config/thresholds"),
            apiCall("/admin/zones"),
        ]);

        document.getElementById("global-threshold-input").value =
            thresholds.global_threshold;
        displayZoneThresholds(zones.zones, thresholds.zone_thresholds);
    } catch (error) {
        console.error("Error loading thresholds:", error);
    }
}

function displayZoneThresholds(zones, zoneThresholds) {
    const container = document.getElementById("zone-thresholds-container");

    if (zones.length === 0) {
        container.innerHTML =
            '<div class="text-muted">No zones available</div>';
        return;
    }

    container.innerHTML = zones
        .map(
            (zone) => `
        <div class="mb-2">
            <label class="form-label small">${zone.name}</label>
            <div class="input-group input-group-sm">
                <input type="number" class="form-control" id="zone-threshold-${
                    zone.name
                }" 
                       value="${
                           zoneThresholds[zone.name] || ""
                       }" placeholder="No threshold" min="1">
                <button class="btn btn-outline-primary" onclick="updateZoneThreshold('${
                    zone.name
                }')">
                    <i class="bi bi-check"></i>
                </button>
            </div>
        </div>
    `
        )
        .join("");
}

async function updateGlobalThreshold() {
    const threshold = parseInt(
        document.getElementById("global-threshold-input").value
    );

    if (isNaN(threshold) || threshold < 1) {
        showNotification("error", "Please enter a valid threshold (minimum 1)");
        return;
    }

    try {
        await apiCall("/admin/config/thresholds", "PUT", {
            global_threshold: threshold,
        });
        showNotification("success", "Global threshold updated");
    } catch (error) {
        console.error("Error updating threshold:", error);
    }
}

async function updateZoneThreshold(zoneName) {
    const input = document.getElementById(`zone-threshold-${zoneName}`);
    const threshold = parseInt(input.value);

    if (isNaN(threshold) || threshold < 1) {
        showNotification("error", "Please enter a valid threshold (minimum 1)");
        return;
    }

    try {
        await apiCall("/admin/config/thresholds", "PUT", {
            zone_thresholds: { [zoneName]: threshold },
        });
        showNotification("success", `Threshold for ${zoneName} updated`);
    } catch (error) {
        console.error("Error updating zone threshold:", error);
    }
}

// ==================== Activity Logs ====================

async function loadLogs() {
    const category = document.getElementById("log-category-filter").value;
    const startDate = document.getElementById("log-start-date").value;
    const endDate = document.getElementById("log-end-date").value;

    let endpoint = "/admin/logs?limit=100";
    if (category) endpoint += `&category=${category}`;
    if (startDate) endpoint += `&start_date=${startDate}T00:00:00`;
    if (endDate) endpoint += `&end_date=${endDate}T23:59:59`;

    try {
        const data = await apiCall(endpoint);
        displayLogs(data.logs);
    } catch (error) {
        console.error("Error loading logs:", error);
    }
}

function displayLogs(logs) {
    const container = document.getElementById("logs-container");

    if (logs.length === 0) {
        container.innerHTML =
            '<div class="text-center text-muted">No logs found</div>';
        return;
    }

    container.innerHTML = logs
        .map(
            (log) => `
        <div class="log-entry ${log.category}">
            <div class="row">
                <div class="col-md-2">
                    <small class="text-muted">${formatDateTime(
                        log.timestamp
                    )}</small>
                </div>
                <div class="col-md-2">
                    <span class="badge bg-secondary">${log.category}</span>
                </div>
                <div class="col-md-3">
                    <strong>${log.action.replace(/_/g, " ")}</strong>
                </div>
                <div class="col-md-2">
                    <small>${log.username || "System"}</small>
                </div>
                <div class="col-md-3">
                    <small class="text-muted">${log.details || "-"}</small>
                </div>
            </div>
        </div>
    `
        )
        .join("");
}

function clearLogFilters() {
    document.getElementById("log-category-filter").value = "";
    document.getElementById("log-start-date").value = "";
    document.getElementById("log-end-date").value = "";
    loadLogs();
}

async function exportLogs(format) {
    const category = document.getElementById("log-category-filter").value;
    const startDate = document.getElementById("log-start-date").value;
    const endDate = document.getElementById("log-end-date").value;

    let endpoint = `/admin/logs/export/${format}?`;
    if (category) endpoint += `category=${category}&`;
    if (startDate) endpoint += `start_date=${startDate}T00:00:00&`;
    if (endDate) endpoint += `end_date=${endDate}T23:59:59&`;

    // Open in new window to download
    window.open(`${API_BASE}${endpoint}`, "_blank");
}

// ==================== Alert History ====================

async function loadAlertHistory() {
    try {
        const data = await apiCall("/admin/alerts/history?limit=100");
        displayAlertHistory(data.alerts);
    } catch (error) {
        console.error("Error loading alert history:", error);
    }
}

function displayAlertHistory(alerts) {
    const tbody = document.getElementById("alerts-table-body");

    if (alerts.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="5" class="text-center text-muted">No alerts recorded</td></tr>';
        return;
    }

    tbody.innerHTML = alerts
        .map(
            (alert) => `
        <tr>
            <td>${formatDateTime(alert.timestamp)}</td>
            <td><span class="badge bg-${
                alert.alert_type === "global" ? "danger" : "warning"
            }">${alert.alert_type}</span></td>
            <td>${alert.threshold}</td>
            <td><strong>${alert.actual_count}</strong></td>
            <td>
                ${
                    alert.acknowledged
                        ? `<span class="badge bg-success">Acknowledged</span>`
                        : `<span class="badge bg-warning">Pending</span>`
                }
            </td>
        </tr>
    `
        )
        .join("");
}

// ==================== Utility Functions ====================

function formatDateTime(dateString) {
    if (!dateString) return "N/A";
    const date = new Date(dateString);
    return date.toLocaleString();
}

function showNotification(type, message) {
    // Create toast notification
    const toast = document.createElement("div");
    toast.className = `alert alert-${type} position-fixed top-0 end-0 m-3`;
    toast.style.zIndex = "9999";
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
    `;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 5000);
}

function logout() {
    // Call logout endpoint
    if (authToken) {
        fetch(`${API_BASE}/auth/logout`, {
            method: "POST",
            headers: {
                Authorization: `Bearer ${authToken}`,
                "Content-Type": "application/json",
            },
        }).catch(console.error);
    }

    // Clear local storage
    localStorage.removeItem("auth_token");
    localStorage.removeItem("user_id");
    localStorage.removeItem("username");
    localStorage.removeItem("user_role");

    // Redirect to login
    window.location.href = "login.html";
}
