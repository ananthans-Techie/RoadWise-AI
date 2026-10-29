// ============================================================
// RoadWise AI — Road Damage Mapping and Photo Geotagging
// File: static/js/map.js
// ============================================================

// 1. Initialise the Leaflet map
const map = L.map("map").setView([8.5241, 76.9366], 13);

const mapMessage = document.getElementById("map-message");
const damageForm = document.getElementById("damage-form");
const selectedLocation = document.getElementById("selected-location");
const selectedLocationBox = document.getElementById("selected-location-box");
const reportMessage = document.getElementById("report-message");
const submitReportBtn = document.getElementById("submit-report-btn");

const reportLatInput = document.getElementById("report-lat");
const reportLngInput = document.getElementById("report-lng");
const damagePhotoInput = document.getElementById("damage-photo");
const damagePhotoPreview = document.getElementById("damage-photo-preview");
const autoFillGpsBtn = document.getElementById("auto-fill-gps-btn");

const roadNameInput = document.getElementById("road-name");
const roadNameAiBadge = document.getElementById("road-name-ai-badge");
const aiMapAnalysisContainer = document.getElementById("ai-map-analysis-container");
const mapAnalysisTierBadge = document.getElementById("map-analysis-tier-badge");
const mapAnalysisContent = document.getElementById("map-analysis-content");
const btnAnalyzeMapLocation = document.getElementById("btn-analyze-map-location");
let mapInspectionCircle = null;

const photoForm = document.getElementById("photo-form");
const photoInput = document.getElementById("road-photo");
const photoPreview = document.getElementById("photo-preview");
const photoMessage = document.getElementById("photo-message");

let selectedCoordinates = null; // { lat: number, lng: number, source: string }
let stagedPhotoFile = null;     // JPEG file linked from photo-form
let photoLocationMarker = null; // Marker dedicated to photo GPS extraction
const submittedReports = [];

// 2. OpenStreetMap basemap
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        'OpenStreetMap contributors</a>'
}).addTo(map);

// 3. Study-area marker
L.marker([8.5241, 76.9366])
    .addTo(map)
    .bindPopup(
        "<strong>RoadWise AI</strong><br>" +
        "Approximate study-area centre<br>" +
        "Thiruvananthapuram, Kerala"
    );

// 4. Marker colours based on severity
const severityColors = {
    Critical: "#ef4444",
    Moderate: "#f59e0b",
    Minor: "#10b981"
};

// 5. Demonstration incidents (illustrative, not field-verified)
const demoIncidents = [
    {
        lat: 8.5248,
        lng: 76.9360,
        type: "Pothole",
        severity: "Critical",
        road: "Demonstration Incident A",
        priority: 90
    },
    {
        lat: 8.5310,
        lng: 76.9430,
        type: "Longitudinal crack",
        severity: "Moderate",
        road: "Demonstration Incident B",
        priority: 55
    },
    {
        lat: 8.5160,
        lng: 76.9290,
        type: "Surface deterioration",
        severity: "Minor",
        road: "Demonstration Incident C",
        priority: 25
    }
];

demoIncidents.forEach((incident) => {
    L.circleMarker([incident.lat, incident.lng], {
        radius: 8,
        color: "#ffffff",
        weight: 2,
        fillColor: severityColors[incident.severity],
        fillOpacity: 0.95
    })
        .addTo(map)
        .bindPopup(`
            <strong>${incident.road}</strong><br>
            Damage: ${incident.type}<br>
            Severity: ${incident.severity}<br>
            Demo priority: ${incident.priority}/100<br>
            <em>Illustrative demonstration incident — not field-verified</em>
        `);
});

// Helper: Escape user input for safe HTML popups
function escapeHTML(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[char]);
}

// 6. Central location updater: keeps coordinates, UI box, marker and inputs in sync
function setSelectedLocation(lat, lng, source, note) {
    const numericLat = Number(lat);
    const numericLng = Number(lng);

    if (!Number.isFinite(numericLat) || !Number.isFinite(numericLng)) {
        console.error("Invalid coordinates provided to setSelectedLocation:", lat, lng);
        return;
    }

    selectedCoordinates = {
        lat: numericLat,
        lng: numericLng,
        source: source || "manual"
    };

    // Update hidden form inputs so they are strictly included in report submission
    if (reportLatInput) reportLatInput.value = numericLat.toFixed(6);
    if (reportLngInput) reportLngInput.value = numericLng.toFixed(6);

    const isPhoto = source === "photo";
    const badgeHtml = isPhoto
        ? '<span class="badge badge-photo">📷 Extracted from photo GPS</span>'
        : '<span class="badge badge-manual">🗺️ Manual map selection</span>';

    if (selectedLocation) {
        selectedLocation.innerHTML = `
            <strong>${numericLat.toFixed(6)}, ${numericLng.toFixed(6)}</strong> ${badgeHtml}<br>
            <span style="font-size: 12.5px; color: #475569;">
                ${note || "Review location marker on map before submitting."}
            </span>
        `;
    }

    if (selectedLocationBox) {
        selectedLocationBox.classList.add("selected");
    }

    if (btnAnalyzeMapLocation) {
        btnAnalyzeMapLocation.style.display = "inline-block";
    }

    // Move or place the visual selection marker on the map
    if (window.selectionMarker) {
        window.selectionMarker.setLatLng([numericLat, numericLng]);
    } else {
        window.selectionMarker = L.circleMarker([numericLat, numericLng], {
            radius: 10,
            color: "#1d4ed8",
            weight: 3,
            fillColor: "#60a5fa",
            fillOpacity: 0.85
        }).addTo(map);
    }

    window.selectionMarker
        .bindPopup(`
            <strong>Selected Report Location</strong><br>
            Latitude: ${numericLat.toFixed(6)}<br>
            Longitude: ${numericLng.toFixed(6)}<br>
            <em>${isPhoto ? "From photo EXIF metadata" : "Manually clicked on map"}</em>
        `)
        .openPopup();
}

// 6b. Location and Map Analysis Helpers
function applyAiLocation(roadName, source) {
    if (!roadName || !roadNameInput) return;
    roadNameInput.value = roadName;
    roadNameInput.dispatchEvent(new Event("input", { bubbles: true }));

    // Visual pulse animation
    roadNameInput.classList.remove("ai-auto-filled-highlight");
    void roadNameInput.offsetWidth;
    roadNameInput.classList.add("ai-auto-filled-highlight");

    if (roadNameAiBadge) {
        let label = "📍 Auto-extracted Location";
        if (source === "watermark_ocr") {
            label = "📸 Extracted from Photo Watermark";
        } else if (source === "nominatim") {
            label = "🌐 Reverse Geocoded from GPS";
        } else if (source === "postgis") {
            label = "🛣️ PostGIS Corridor Matched";
        }
        roadNameAiBadge.textContent = label;
        roadNameAiBadge.className = "ai-inline-badge";
        roadNameAiBadge.style.display = "inline-block";
    }
}

if (roadNameInput) {
    roadNameInput.addEventListener("input", (e) => {
        // If the user manually types, update badge
        if (e.isTrusted && roadNameAiBadge && roadNameAiBadge.style.display !== "none") {
            roadNameAiBadge.textContent = "✏️ Manually edited";
            roadNameAiBadge.className = "ai-inline-badge ai-pill-manual";
        }
    });
}

function clearMapAnalysis() {
    if (aiMapAnalysisContainer) {
        aiMapAnalysisContainer.style.display = "none";
    }
    if (mapInspectionCircle) {
        map.removeLayer(mapInspectionCircle);
        mapInspectionCircle = null;
    }
    if (btnAnalyzeMapLocation) {
        btnAnalyzeMapLocation.style.display = "none";
    }
}

function renderMapAnalysis(rawAnalysis, lat, lng) {
    if (!rawAnalysis) return;
    const analysis = rawAnalysis.analysis || rawAnalysis;

    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        if (analysis.latitude !== undefined && Number.isFinite(Number(analysis.latitude))) {
            lat = Number(analysis.latitude);
            lng = Number(analysis.longitude);
        } else if (analysis.coordinates && Number.isFinite(analysis.coordinates.lat)) {
            lat = analysis.coordinates.lat;
            lng = analysis.coordinates.lng;
        } else if (selectedCoordinates) {
            lat = selectedCoordinates.lat;
            lng = selectedCoordinates.lng;
        } else {
            return;
        }
    }

    // 1. Draw or update inspection circle on Leaflet map
    if (mapInspectionCircle) {
        map.removeLayer(mapInspectionCircle);
    }
    mapInspectionCircle = L.circle([lat, lng], {
        radius: 250,
        color: "#2563eb",
        dashArray: "6, 6",
        weight: 2,
        fillColor: "#3b82f6",
        fillOpacity: 0.12
    }).addTo(map);
    mapInspectionCircle.bindTooltip("AI GIS Analysis Radius (250m)", { sticky: true });

    // 2. Ensure Analyze button is visible
    if (btnAnalyzeMapLocation) {
        btnAnalyzeMapLocation.style.display = "inline-block";
    }

    // 3. Populate analysis container
    if (aiMapAnalysisContainer) {
        aiMapAnalysisContainer.style.display = "block";
    }

    const priority = analysis.estimated_priority || analysis.priority_evaluation || {};
    const tier = priority.tier || "Medium";
    const score = priority.score !== undefined ? Number(priority.score).toFixed(1) : "50.0";
    const breakdown = priority.breakdown || {};
    const baseSev = breakdown.severity_points !== undefined ? breakdown.severity_points : (breakdown.base_severity || 0);
    const roadPts = breakdown.road_class_points !== undefined ? breakdown.road_class_points : (breakdown.corridor_weight || 0);
    const trafPts = breakdown.traffic_points !== undefined ? breakdown.traffic_points : (breakdown.traffic_weight || 0);
    const clustPts = breakdown.cluster_points !== undefined ? breakdown.cluster_points : (breakdown.cluster_density || 0);

    if (mapAnalysisTierBadge) {
        mapAnalysisTierBadge.textContent = `${tier} Priority (${score}/100)`;
        mapAnalysisTierBadge.className = `priority-tier-badge ${tier === "High" ? "priority-high" : (tier === "Medium" ? "priority-medium" : "priority-low")}`;
    }

    const corridorName = analysis.display_road_name || analysis.road_name || (analysis.corridor && analysis.corridor.name) || "Identified Road Corridor";
    const classification = analysis.road_classification || (analysis.corridor && analysis.corridor.classification) || "Arterial / Urban Collector";
    const speedLimit = (analysis.corridor && analysis.corridor.speed_limit) || 50;

    const aadtVal = analysis.traffic_aadt ? Number(analysis.traffic_aadt).toLocaleString() : (analysis.traffic?.estimated_aadt ? Number(analysis.traffic.estimated_aadt).toLocaleString() : "15,000");
    const trafficLevel = analysis.traffic_level || analysis.traffic?.traffic_level || "Moderate Flow Urban Collector";

    const clusterText = analysis.cluster_status || (analysis.spatial_context?.hotspot_cluster ? "Active Hotspot Cluster" : "Isolated Incident");
    const nearbyCount = analysis.nearby_incidents_count !== undefined ? analysis.nearby_incidents_count : (analysis.spatial_context?.nearby_reports_500m || 0);
    const junctions = analysis.junctions_within_200m !== undefined ? analysis.junctions_within_200m : (analysis.spatial_context?.junction_count || 0);

    let recAction = "";
    let recTreatment = "";
    let recRationale = "";
    let recUrgency = tier === "High" ? "Urgent (Within 48h)" : (tier === "Medium" ? "Scheduled (Within 14 Days)" : "Routine Maintenance");

    if (typeof analysis.engineering_recommendation === "string") {
        recAction = analysis.engineering_recommendation;
        recTreatment = "Asphalt surface profiling and mechanical patching.";
        recRationale = `Prioritised based on corridor classification (${classification}) and traffic volume (~${aadtVal} veh/day).`;
    } else if (analysis.engineering_recommendation) {
        const r = analysis.engineering_recommendation;
        recAction = r.action || "Standard pavement maintenance.";
        recTreatment = r.treatment || "Cold milling and asphalt patch.";
        recRationale = r.rationale || `Calculated from traffic density and corridor class.`;
        if (r.urgency) recUrgency = r.urgency;
    } else {
        recAction = "Scheduled asphalt resurfacing and crack filling.";
        recTreatment = "Routine hot-mix asphalt patching.";
        recRationale = "Standard road maintenance queue.";
    }

    const tierClass = tier === "High" ? "priority-high" : (tier === "Medium" ? "priority-med" : "priority-low");
    const barColor = tier === "High" ? "#ef4444" : (tier === "Medium" ? "#f59e0b" : "#10b981");
    const urgencyColor = tier === "High" ? "#b91c1c" : (tier === "Medium" ? "#b45309" : "#047857");
    const pct = Math.min(100, Math.max(8, Number(score) || 0));

    if (mapAnalysisContent) {
        mapAnalysisContent.innerHTML = `
            <div class="map-spatial-grid">
                <div class="map-spatial-item">
                    <div class="label">Road Corridor</div>
                    <div class="value">${escapeHTML(corridorName)}</div>
                    <div class="subtext">${escapeHTML(classification)} &bull; ${speedLimit} km/h</div>
                </div>
                <div class="map-spatial-item">
                    <div class="label">Traffic Volume (Est.)</div>
                    <div class="value">~${aadtVal} veh/day</div>
                    <div class="subtext">${escapeHTML(trafficLevel)}</div>
                </div>
                <div class="map-spatial-item">
                    <div class="label">Spatial Context (500m)</div>
                    <div class="value">${nearbyCount} nearby reports</div>
                    <div class="subtext">${escapeHTML(clusterText)} &bull; ${junctions} junctions</div>
                </div>
            </div>

            <div class="priority-meter-box" style="margin-top: 10px; margin-bottom: 10px; padding: 10px; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0;">
                <div class="priority-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 13px;"><strong>Estimated Priority Score:</strong> ${score} / 100</span>
                    <span class="priority-tier-badge ${tierClass}" style="font-size: 11px;">${escapeHTML(tier)} Tier</span>
                </div>
                <div class="priority-bar-wrap" style="height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden;">
                    <div class="priority-bar-fill" style="width: ${pct}%; background: ${barColor}; height: 100%; transition: width 0.4s ease;"></div>
                </div>
                <div class="priority-breakdown-details" style="font-size: 11.5px; color: #475569; margin-top: 6px;">
                    <strong>Factor Contribution:</strong> Severity: ${baseSev} pts | Corridor Class: ${roadPts} pts | Traffic: ${trafPts} pts | Clusters: ${clustPts} pts
                </div>
            </div>

            <div class="map-recommendation-box">
                <div class="title" style="display: flex; justify-content: space-between; align-items: center;">
                    <span>🛠️ AI Engineering Recommendation:</span>
                    <span style="font-size: 12px; color: ${urgencyColor}; font-weight: 700;">${escapeHTML(recUrgency)}</span>
                </div>
                <div style="font-weight: 600; font-size: 13px; color: #0f172a; margin-bottom: 4px;">${escapeHTML(recAction)}</div>
                <div class="treatment" style="font-size: 12px; color: #334155; margin-bottom: 3px;"><strong>Treatment:</strong> ${escapeHTML(recTreatment)}</div>
                <div class="rationale" style="font-size: 11.5px; color: #64748b;"><em>Rationale:</em> ${escapeHTML(recRationale)}</div>
            </div>
        `;
    }

    // Enrich selectionMarker popup if available
    if (window.selectionMarker) {
        window.selectionMarker.bindPopup(`
            <div class="map-analysis-popup" style="min-width: 190px;">
                <strong style="color:#0f172a;">Selected Incident Location</strong><br>
                <span><strong>Coordinates:</strong> ${lat.toFixed(6)}, ${lng.toFixed(6)}</span><br>
                <span><strong>Corridor:</strong> ${escapeHTML(corridorName)}</span><br>
                <span><strong>Traffic:</strong> ~${aadtVal} veh/day</span><br>
                <div style="margin-top:6px; padding-top:4px; border-top:1px solid #e2e8f0;">
                    <strong>AI Priority:</strong> <span style="font-weight:bold; color:${barColor};">${score}/100 (${tier})</span><br>
                    <small style="color:#0284c7; display:block; margin-top:2px;">${escapeHTML(recAction)}</small>
                </div>
            </div>
        `).openPopup();
    }
}

async function analyzeMapLocation(lat, lng, damageType, severity) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

    if (btnAnalyzeMapLocation) {
        btnAnalyzeMapLocation.disabled = true;
        btnAnalyzeMapLocation.textContent = "Analyzing map location...";
    }

    try {
        const damageTypeSelect = document.getElementById("damage-type");
        const severitySelect = document.getElementById("severity");
        const dType = damageType || (damageTypeSelect ? damageTypeSelect.value : "") || "Pothole";
        const sev = severity || (severitySelect ? severitySelect.value : "") || "Moderate";

        const url = `/api/map/analyze-location?lat=${lat}&lng=${lng}&damage_type=${encodeURIComponent(dType)}&severity=${encodeURIComponent(sev)}`;
        const res = await fetch(url);
        const data = await res.json();

        if (res.ok && (data.status === "ok" || data.status === "success" || data.analysis)) {
            renderMapAnalysis(data.analysis || data, lat, lng);
            // If road name input is currently empty and analysis matched a corridor, auto-populate it
            if (roadNameInput && (!roadNameInput.value || roadNameInput.value.trim() === "")) {
                const autoName = data.analysis?.display_road_name || data.analysis?.road_name;
                if (autoName) {
                    applyAiLocation(autoName, "postgis");
                }
            }
        }
    } catch (err) {
        console.warn("Map analysis fetch error:", err);
    } finally {
        if (btnAnalyzeMapLocation) {
            btnAnalyzeMapLocation.disabled = false;
            btnAnalyzeMapLocation.textContent = "🗺️ Analyze Location on Map";
            btnAnalyzeMapLocation.style.display = "inline-block";
        }
    }
}

if (btnAnalyzeMapLocation) {
    btnAnalyzeMapLocation.addEventListener("click", () => {
        if (selectedCoordinates) {
            const damageTypeSelect = document.getElementById("damage-type");
            const severitySelect = document.getElementById("severity");
            analyzeMapLocation(
                selectedCoordinates.lat,
                selectedCoordinates.lng,
                damageTypeSelect ? damageTypeSelect.value : null,
                severitySelect ? severitySelect.value : null
            );
        }
    });
}

// 7. Manual map click handler
map.on("click", (event) => {
    setSelectedLocation(
        event.latlng.lat,
        event.latlng.lng,
        "manual",
        "Coordinates set by clicking map. Complete the report fields to submit."
    );

    reportMessage.textContent =
        "Location selected. Review on the map and complete your report.";
    reportMessage.className = "message-info";

    const damageTypeSelect = document.getElementById("damage-type");
    const severitySelect = document.getElementById("severity");
    analyzeMapLocation(
        event.latlng.lat,
        event.latlng.lng,
        damageTypeSelect ? damageTypeSelect.value : null,
        severitySelect ? severitySelect.value : null
    );
});

// 8. Photo GPS extraction tool (#photo-form)
let photoPreviewUrl = null;

photoInput.addEventListener("change", () => {
    const file = photoInput.files[0];

    if (photoPreviewUrl) {
        URL.revokeObjectURL(photoPreviewUrl);
        photoPreviewUrl = null;
    }

    photoPreview.hidden = true;
    photoPreview.removeAttribute("src");
    photoMessage.textContent = "";

    if (!file) return;

    if (!file.name.toLowerCase().match(/\.(jpe?g)$/i) || file.size > 10 * 1024 * 1024) {
        photoMessage.textContent = "Please select a JPEG image (.jpg or .jpeg) no larger than 10 MB.";
        photoMessage.className = "message-error";
        photoInput.value = "";
        return;
    }

    photoPreviewUrl = URL.createObjectURL(file);
    photoPreview.src = photoPreviewUrl;
    photoPreview.hidden = false;
});

photoForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const file = photoInput.files[0];
    if (!file) {
        photoMessage.textContent = "Please choose a JPEG photograph first.";
        photoMessage.className = "message-error";
        return;
    }

    if (!file.name.toLowerCase().match(/\.(jpe?g)$/i) || file.size > 10 * 1024 * 1024) {
        photoMessage.textContent = "Please select a JPEG image under 10 MB.";
        photoMessage.className = "message-error";
        return;
    }

    const formData = new FormData();
    formData.append("photo", file);

    const submitBtn = photoForm.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;

    photoMessage.textContent = "Uploading photo and inspecting EXIF GPS metadata...";
    photoMessage.className = "message-info";

    try {
        const response = await fetch("/api/geotag", {
            method: "POST",
            body: formData
        });

        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            const errorHtml = await response.text();
            console.error("Non-JSON response from /api/geotag:", response.status, errorHtml);
            throw new Error(`Server returned non-JSON response (HTTP ${response.status}). Check Flask terminal.`);
        }

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Failed to extract GPS metadata.");
        }

        const lat = Number(result.latitude);
        const lng = Number(result.longitude);

        if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
            throw new Error("Server returned coordinates outside valid geographical limits.");
        }

        // Stage this file to be attached to the damage report form
        stagedPhotoFile = file;

        // Auto-populate road name if extracted from photo watermark / reverse geocoding
        if (result.road_name) {
            applyAiLocation(result.road_name, result.location_source);
        }

        // Auto-populate report location
        setSelectedLocation(
            lat,
            lng,
            "photo",
            "Extracted from photo. Review on map or click map to adjust."
        );

        // Center map to extracted location
        map.setView([lat, lng], 17);

        // Remove previous photo marker and create fresh one
        if (photoLocationMarker) {
            map.removeLayer(photoLocationMarker);
        }

        photoLocationMarker = L.marker([lat, lng])
            .addTo(map)
            .bindPopup(`
                <strong>Geotagged Photo Location</strong><br>
                Latitude: ${lat.toFixed(6)}<br>
                Longitude: ${lng.toFixed(6)}<br>
                ${result.road_name ? `<strong>Road:</strong> ${escapeHTML(result.road_name)}<br>` : ""}
                <em>Coordinates extracted and linked to Damage Report</em>
            `)
            .openPopup();

        // Perform or render map spatial analysis
        if (result.map_analysis) {
            renderMapAnalysis(result.map_analysis, lat, lng);
        } else {
            analyzeMapLocation(lat, lng);
        }

        // Mirror photo preview in the damage report form
        if (damagePhotoPreview) {
            if (damagePhotoPreviewUrl) URL.revokeObjectURL(damagePhotoPreviewUrl);
            damagePhotoPreviewUrl = URL.createObjectURL(file);
            damagePhotoPreview.src = damagePhotoPreviewUrl;
            damagePhotoPreview.hidden = false;
            damagePhotoPreview.style.display = "block";
        }

        const damagePhotoHint = document.getElementById("damage-photo-hint");
        if (damagePhotoHint) {
            damagePhotoHint.innerHTML = `<strong>Attached Photo:</strong> ${escapeHTML(file.name)} (${(file.size / 1024).toFixed(1)} KB) — GPS location auto-populated below.`;
        }

        // Trigger AI road-damage detection analysis (Stage 4)
        analyzePhotoForDamage(file);

        photoMessage.textContent =
            `GPS found: ${lat.toFixed(6)}, ${lng.toFixed(6)}. ` +
            "Location and photo auto-populated in Road Damage Report below!";
        photoMessage.className = "message-success";

        reportMessage.textContent =
            `Location auto-populated from photo (${lat.toFixed(6)}, ${lng.toFixed(6)}). ` +
            "Complete the road details and submit your report.";
        reportMessage.className = "message-info";

        if (selectedLocationBox) {
            selectedLocationBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }

    } catch (err) {
        console.error("Geotagging error:", err);
        photoMessage.textContent = err.message || "Failed to extract GPS from photo.";
        photoMessage.className = "message-error";
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
});

// 9. AI Road-Damage Detection & Damage Form Photo Preview (Stage 4)
let damagePhotoPreviewUrl = null;
let currentDetections = [];
let currentPredictedSeverity = null;

function clearAiDetection() {
    currentDetections = [];
    currentPredictedSeverity = null;
    clearMapAnalysis();
    if (roadNameAiBadge) roadNameAiBadge.style.display = "none";
    if (roadNameInput) roadNameInput.classList.remove("ai-auto-filled-highlight");
    const container = document.getElementById("ai-detection-container");
    const canvas = document.getElementById("ai-bbox-canvas");
    const feedback = document.getElementById("ai-feedback-text");
    const statusBadge = document.getElementById("ai-detection-status");
    const inlineBadge = document.getElementById("damage-type-ai-badge");
    const severityBadge = document.getElementById("severity-ai-badge");
    const damageTypeSelect = document.getElementById("damage-type");
    const severitySelect = document.getElementById("severity");

    if (container) container.style.display = "none";
    if (canvas) {
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    if (feedback) feedback.textContent = "";
    if (statusBadge) {
        statusBadge.className = "ai-status-badge ai-status-pending";
        statusBadge.textContent = "Scanning image...";
    }
    if (inlineBadge) inlineBadge.style.display = "none";
    if (severityBadge) severityBadge.style.display = "none";
    if (damageTypeSelect) damageTypeSelect.classList.remove("ai-auto-filled-highlight");
    if (severitySelect) severitySelect.classList.remove("ai-auto-filled-highlight");
}

function drawBoundingBoxes(detections) {
    const canvas = document.getElementById("ai-bbox-canvas");
    const img = document.getElementById("damage-photo-preview");
    if (!canvas || !img) return;

    const displayW = img.clientWidth;
    const displayH = img.clientHeight;
    if (!displayW || !displayH) return;

    canvas.width = displayW;
    canvas.height = displayH;
    canvas.style.width = displayW + "px";
    canvas.style.height = displayH + "px";

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!detections || detections.length === 0) return;

    const naturalW = img.naturalWidth || displayW;
    const naturalH = img.naturalHeight || displayH;
    const scaleX = displayW / naturalW;
    const scaleY = displayH / naturalH;

    detections.forEach((det) => {
        if (!det.box || det.box.length < 4) return;
        const [xmin, ymin, xmax, ymax] = det.box;
        const x = xmin * scaleX;
        const y = ymin * scaleY;
        const w = (xmax - xmin) * scaleX;
        const h = (ymax - ymin) * scaleY;

        ctx.lineWidth = 2.5;
        ctx.strokeStyle = "#ea580c";
        ctx.strokeRect(x, y, w, h);

        const label = `${det.class} ${(det.confidence * 100).toFixed(0)}%`;
        ctx.font = "bold 11px sans-serif";
        const textWidth = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(234, 88, 12, 0.9)";
        ctx.fillRect(x, Math.max(0, y - 18), textWidth + 8, 18);

        ctx.fillStyle = "#ffffff";
        ctx.fillText(label, x + 4, Math.max(12, y - 5));
    });
}

function scheduleDrawBoundingBoxes() {
    requestAnimationFrame(() => {
        setTimeout(() => {
            drawBoundingBoxes(currentDetections);
        }, 50);
    });
}

async function analyzePhotoForDamage(file) {
    const container = document.getElementById("ai-detection-container");
    const statusBadge = document.getElementById("ai-detection-status");
    const feedback = document.getElementById("ai-feedback-text");
    const damageTypeSelect = document.getElementById("damage-type");

    if (!container || !file) return;

    container.style.display = "block";
    if (statusBadge) {
        statusBadge.className = "ai-status-badge ai-status-pending";
        statusBadge.textContent = "AI Scanning...";
    }
    if (feedback) {
        feedback.innerHTML = "<em>Analyzing photograph for road damage...</em>";
    }

    const formData = new FormData();
    formData.append("photo", file);

    try {
        const response = await fetch("/api/detect", {
            method: "POST",
            body: formData,
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Detection analysis failed.");
        }

        if (data.status === "info" || data.status === "unavailable") {
            if (statusBadge) {
                statusBadge.className = "ai-status-badge ai-status-info";
                statusBadge.textContent = "Model Offline";
            }
            if (feedback) {
                feedback.innerHTML = `<strong>Note:</strong> ${escapeHTML(data.message || "Model weights not yet loaded. Please select damage type manually.")}`;
            }
            return;
        }

        currentDetections = data.detections || [];

        // Auto-populate road name if detected from photo watermark or reverse geocoding
        if (data.road_name) {
            applyAiLocation(data.road_name, data.location_source);
        }

        // Auto-populate incident location coordinates if detected
        if (data.latitude !== null && data.latitude !== undefined && data.longitude !== null && data.longitude !== undefined) {
            const lat = Number(data.latitude);
            const lng = Number(data.longitude);
            if (Number.isFinite(lat) && Number.isFinite(lng)) {
                setSelectedLocation(
                    lat,
                    lng,
                    "photo",
                    `Auto-extracted from photo (${data.location_source === "watermark_ocr" ? "Watermark OCR" : "EXIF GPS"}).`
                );
                map.setView([lat, lng], 17);

                if (photoLocationMarker) {
                    map.removeLayer(photoLocationMarker);
                }
                photoLocationMarker = L.marker([lat, lng])
                    .addTo(map)
                    .bindPopup(`
                        <strong>Geotagged Photo Location</strong><br>
                        Latitude: ${lat.toFixed(6)}<br>
                        Longitude: ${lng.toFixed(6)}<br>
                        ${data.road_name ? `<strong>Road:</strong> ${escapeHTML(data.road_name)}<br>` : ""}
                        <em>Coordinates & Location auto-populated from photo</em>
                    `)
                    .openPopup();

                if (data.map_analysis) {
                    renderMapAnalysis(data.map_analysis, lat, lng);
                } else {
                    analyzeMapLocation(lat, lng, data.suggested_damage_type, data.suggested_severity);
                }
            }
        }

        const img = document.getElementById("damage-photo-preview");
        if (img) {
            if (img.complete && img.naturalWidth > 0) {
                scheduleDrawBoundingBoxes();
            } else {
                img.addEventListener("load", scheduleDrawBoundingBoxes, { once: true });
            }
        }

        if (currentDetections.length > 0 && data.suggested_damage_type) {
            if (statusBadge) {
                statusBadge.className = "ai-status-badge ai-status-success";
                statusBadge.textContent = `${currentDetections.length} Detected`;
            }

            // Auto-select predicted damage type in the form dropdown and fire change event
            const inlineBadge = document.getElementById("damage-type-ai-badge");
            const severityBadge = document.getElementById("severity-ai-badge");
            const severitySelect = document.getElementById("severity");
            const confPercent = (data.confidence * 100).toFixed(0);

            if (damageTypeSelect) {
                damageTypeSelect.value = data.suggested_damage_type;
                damageTypeSelect.dispatchEvent(new Event("change", { bubbles: true }));

                // Add visual highlight animation
                damageTypeSelect.classList.remove("ai-auto-filled-highlight");
                void damageTypeSelect.offsetWidth; // Force DOM reflow
                damageTypeSelect.classList.add("ai-auto-filled-highlight");
            }

            if (inlineBadge) {
                inlineBadge.textContent = `🎯 AI Predicted: ${data.suggested_damage_type} (${confPercent}%)`;
                inlineBadge.className = "ai-inline-badge";
                inlineBadge.style.display = "inline-block";
            }

            // Auto-select predicted severity based on confidence & damage density
            if (data.suggested_severity) {
                currentPredictedSeverity = data.suggested_severity;

                if (severitySelect) {
                    severitySelect.value = data.suggested_severity;
                    severitySelect.dispatchEvent(new Event("change", { bubbles: true }));

                    severitySelect.classList.remove("ai-auto-filled-highlight");
                    void severitySelect.offsetWidth;
                    severitySelect.classList.add("ai-auto-filled-highlight");
                }

                if (severityBadge) {
                    severityBadge.textContent = `⚡ AI Predicted: ${data.suggested_severity}`;
                    severityBadge.className = "ai-inline-badge";
                    severityBadge.style.display = "inline-block";
                }
            }

            if (feedback) {
                const severityClass = (data.suggested_severity || "").toLowerCase();
                const severityBadgeHtml = data.suggested_severity
                    ? `<span class="ai-severity-badge ai-severity-${severityClass}">⚡ Severity: ${escapeHTML(data.suggested_severity)}</span>`
                    : "";

                feedback.innerHTML = `
                    <div class="ai-prediction-card">
                        <div class="ai-prediction-header">
                            <span class="ai-prediction-badge">🎯 AI Predicted: ${escapeHTML(data.suggested_damage_type)}</span>
                            <span class="ai-conf-meter">${confPercent}% Confidence</span>
                            ${severityBadgeHtml}
                        </div>
                        <p class="ai-prediction-desc">
                            Detected <strong>${currentDetections.length}</strong> damage zone(s) on the photo.
                            The <strong>Damage type</strong> (${escapeHTML(data.suggested_damage_type)}) and <strong>Severity</strong> (${escapeHTML(data.suggested_severity || "Evaluated")}) fields have been <strong>automatically populated</strong>.
                        </p>
                    </div>
                `;
            }
        } else {
            if (statusBadge) {
                statusBadge.className = "ai-status-badge ai-status-none";
                statusBadge.textContent = "No Damage Detected";
            }
            if (feedback) {
                let candidateDetail = "";
                if (data.highest_candidate && data.highest_candidate.class) {
                    const candConf = (data.highest_candidate.confidence * 100).toFixed(1);
                    candidateDetail = ` Closest candidate: <em>${escapeHTML(data.highest_candidate.class)}</em> (${candConf}%).`;
                }
                const threshText = data.threshold_used ? ` above ${(data.threshold_used * 100).toFixed(0)}% threshold` : "";
                feedback.innerHTML = `
                    <em>No road damage detected${threshText}.${candidateDetail} Please verify and select damage type manually in the dropdown above.</em>
                `;
            }
        }
    } catch (err) {
        console.warn("AI detection error:", err);
        if (statusBadge) {
            statusBadge.className = "ai-status-badge ai-status-none";
            statusBadge.textContent = "Detector Offline";
        }
        if (feedback) {
            feedback.innerHTML = `
                <em style="color:#64748b;">AI suggestion unavailable. Please select damage type manually.</em>
            `;
        }
    }
}

// User manual override listener for damage-type dropdown
const damageTypeSelectEl = document.getElementById("damage-type");
if (damageTypeSelectEl) {
    damageTypeSelectEl.addEventListener("change", () => {
        const inlineBadge = document.getElementById("damage-type-ai-badge");
        if (inlineBadge && inlineBadge.style.display !== "none") {
            if (currentDetections && currentDetections.length > 0 && damageTypeSelectEl.value !== currentDetections[0].class) {
                inlineBadge.textContent = `✏️ Manually changed to: ${damageTypeSelectEl.value || "None"}`;
                inlineBadge.className = "ai-inline-badge ai-pill-manual";
            }
        }
        if (selectedCoordinates && aiMapAnalysisContainer && aiMapAnalysisContainer.style.display !== "none") {
            analyzeMapLocation(selectedCoordinates.lat, selectedCoordinates.lng, damageTypeSelectEl.value, severitySelectEl ? severitySelectEl.value : null);
        }
    });
}

// User manual override listener for severity dropdown
const severitySelectEl = document.getElementById("severity");
if (severitySelectEl) {
    severitySelectEl.addEventListener("change", () => {
        const severityBadge = document.getElementById("severity-ai-badge");
        if (severityBadge && severityBadge.style.display !== "none") {
            if (currentPredictedSeverity && severitySelectEl.value !== currentPredictedSeverity) {
                severityBadge.textContent = `✏️ Manually changed to: ${severitySelectEl.value || "None"}`;
                severityBadge.className = "ai-inline-badge ai-pill-manual";
            }
        }
        if (selectedCoordinates && aiMapAnalysisContainer && aiMapAnalysisContainer.style.display !== "none") {
            analyzeMapLocation(selectedCoordinates.lat, selectedCoordinates.lng, damageTypeSelectEl ? damageTypeSelectEl.value : null, severitySelectEl.value);
        }
    });
}

window.addEventListener("resize", () => {
    if (currentDetections && currentDetections.length > 0) {
        drawBoundingBoxes(currentDetections);
    }
});

if (damagePhotoInput) {
    damagePhotoInput.addEventListener("change", () => {
        const file = damagePhotoInput.files[0];

        if (damagePhotoPreviewUrl) {
            URL.revokeObjectURL(damagePhotoPreviewUrl);
            damagePhotoPreviewUrl = null;
        }

        clearAiDetection();

        if (damagePhotoPreview) {
            damagePhotoPreview.hidden = true;
            damagePhotoPreview.style.display = "none";
            damagePhotoPreview.removeAttribute("src");
        }

        if (autoFillGpsBtn) {
            autoFillGpsBtn.style.display = "none";
        }

        if (!file) return;

        if (!file.name.toLowerCase().match(/\.(jpe?g)$/i) || file.size > 10 * 1024 * 1024) {
            reportMessage.textContent = "Attached photograph must be a JPEG image under 10 MB.";
            reportMessage.className = "message-error";
            damagePhotoInput.value = "";
            return;
        }

        damagePhotoPreviewUrl = URL.createObjectURL(file);
        if (damagePhotoPreview) {
            damagePhotoPreview.src = damagePhotoPreviewUrl;
            damagePhotoPreview.hidden = false;
            damagePhotoPreview.style.display = "block";
        }

        if (autoFillGpsBtn) {
            autoFillGpsBtn.style.display = "inline-block";
        }

        stagedPhotoFile = file;

        // Run AI damage detection on user-attached photo
        analyzePhotoForDamage(file);
    });
}

if (autoFillGpsBtn) {
    autoFillGpsBtn.addEventListener("click", async () => {
        const file = damagePhotoInput && damagePhotoInput.files[0];
        if (!file) return;

        autoFillGpsBtn.disabled = true;
        autoFillGpsBtn.textContent = "Extracting GPS...";
        reportMessage.textContent = "Reading GPS metadata from attached photo...";
        reportMessage.className = "message-info";

        const formData = new FormData();
        formData.append("photo", file);

        try {
            const res = await fetch("/api/geotag", { method: "POST", body: formData });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "No GPS metadata found.");

            const lat = Number(data.latitude);
            const lng = Number(data.longitude);
            if (data.road_name) {
                applyAiLocation(data.road_name, data.location_source);
            }
            setSelectedLocation(lat, lng, "photo", "Auto-extracted from attached photo.");
            map.setView([lat, lng], 17);

            if (data.map_analysis) {
                renderMapAnalysis(data.map_analysis, lat, lng);
            } else {
                analyzeMapLocation(lat, lng);
            }

            reportMessage.textContent = `Location auto-filled from photo: ${lat.toFixed(6)}, ${lng.toFixed(6)}.`;
            reportMessage.className = "message-success";
        } catch (err) {
            reportMessage.textContent = err.message || "Could not read GPS from this photo.";
            reportMessage.className = "message-error";
        } finally {
            autoFillGpsBtn.disabled = false;
            autoFillGpsBtn.textContent = "📍 Auto-populate report location from this photo's GPS";
        }
    });
}

// 10. Marker Tracking & Report Card Feed Rendering
const reportMarkers = {};

window.focusReportOnMap = function(reportId, lat, lng) {
    const mapEl = document.getElementById("map");
    if (mapEl) {
        mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    map.setView([lat, lng], 17);
    const marker = reportMarkers[reportId];
    if (marker) {
        setTimeout(() => {
            marker.openPopup();
        }, 350);
    }
};

function renderReportCard(report) {
    const noReportsMsg = document.getElementById("no-reports-msg");
    if (noReportsMsg) {
        noReportsMsg.remove();
    }

    const reportsCountBadge = document.getElementById("reports-count-badge");
    if (reportsCountBadge) {
        const count = submittedReports.length;
        reportsCountBadge.textContent = `${count} ${count === 1 ? "report" : "reports"}`;
    }

    const reportsList = document.getElementById("reports-list");
    if (!reportsList) return;

    if (document.getElementById(`report-card-${report.id}`)) return;

    const card = document.createElement("div");
    card.id = `report-card-${report.id}`;
    card.className = "report-card";

    const sevColor = severityColors[report.severity] || "#64748b";

    let photoHtml = "";
    if (report.photo_url) {
        photoHtml = `
            <a href="${report.photo_url}" target="_blank" class="report-card-img-wrap" title="Click to open full-resolution image in new tab">
                <img src="${report.photo_url}" alt="Report photo" class="report-card-img" loading="lazy">
            </a>
        `;
    }

    // Stage 5: Priority score meter & explainable factor breakdown
    let priorityHtml = "";
    if (report.priority_tier || (report.priority_score !== null && report.priority_score !== undefined)) {
        const tier = report.priority_tier || "Low";
        const score = report.priority_score !== null && report.priority_score !== undefined
            ? Number(report.priority_score).toFixed(1)
            : "0.0";
        const tierClass = tier === "High" ? "priority-high" : (tier === "Medium" ? "priority-med" : "priority-low");
        const barColor = tier === "High" ? "#ef4444" : (tier === "Medium" ? "#f59e0b" : "#64748b");
        const pct = Math.min(100, Math.max(6, Number(score) || 0));

        let breakdownHtml = "";
        if (report.explanation) {
            breakdownHtml = `
                <div class="priority-breakdown-details">
                    ${escapeHTML(report.explanation)}
                </div>
            `;
        }

        priorityHtml = `
            <div class="priority-meter-box">
                <div class="priority-header">
                    <span><strong>Priority Score:</strong> ${score} / 100</span>
                    <span class="priority-tier-badge ${tierClass}">${escapeHTML(tier)}</span>
                </div>
                <div class="priority-bar-wrap">
                    <div class="priority-bar-fill" style="width:${pct}%; background:${barColor};"></div>
                </div>
                ${breakdownHtml}
                <small class="priority-disclaimer">Decision support for human review; not an official engineering assessment.</small>
            </div>
        `;
    }

    let woHeaderBadge = "";
    let woActionBtn = "";
    if (report.work_order && report.work_order.code) {
        const stCls = (report.work_order.status || "").toLowerCase().replace(/\s+/g, "-");
        woHeaderBadge = `<span class="report-wo-badge status-${stCls}">📋 ${escapeHTML(report.work_order.code)} &bull; ${escapeHTML(report.work_order.status)}</span>`;
        woActionBtn = `<button type="button" class="btn-wo-action" style="margin-top: 6px; font-size:12px; width:100%; justify-content:center;" onclick="openWorkOrderModal(${report.work_order.id})">📋 View Work Order (${escapeHTML(report.work_order.code)})</button>`;
    }

    card.innerHTML = `
        <div class="report-card-header">
            <div style="display:flex; align-items:center;">
                <span style="font-weight:700; color:#0f766e; font-size:13px;">Report #${report.id}</span>
                ${woHeaderBadge}
            </div>
            <span class="badge" style="background:${sevColor}22; color:${sevColor}; border:1px solid ${sevColor};">
                ${escapeHTML(report.severity)}
            </span>
        </div>
        ${photoHtml}
        <h4 class="report-card-title">${escapeHTML(report.road_name)}</h4>
        <div class="report-card-details">
            <div><strong>Damage:</strong> ${escapeHTML(report.damage_type)}</div>
            ${report.description ? `<div><strong>Details:</strong> ${escapeHTML(report.description)}</div>` : ""}
            <div><strong>Coordinates:</strong> ${report.latitude.toFixed(6)}, ${report.longitude.toFixed(6)}</div>
            <div style="font-size:11.5px; color:#94a3b8; margin-top:4px;">${escapeHTML(report.created_at)}</div>
        </div>
        ${priorityHtml}
        <div style="display:flex; flex-direction:column; gap:4px; margin-top:10px;">
            <button type="button" class="report-card-btn" onclick="focusReportOnMap(${report.id}, ${report.latitude}, ${report.longitude})">
                📍 View on Map
            </button>
            ${woActionBtn}
        </div>
    `;

    reportsList.prepend(card);
}

// 11. Submit road-damage report (#damage-form)
damageForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    // Strict validation: coordinates must be explicitly selected
    if (
        !selectedCoordinates ||
        !Number.isFinite(selectedCoordinates.lat) ||
        !Number.isFinite(selectedCoordinates.lng)
    ) {
        reportMessage.textContent =
            "Please select a location first by clicking on the map or extracting GPS from a photo.";
        reportMessage.className = "message-error";
        if (selectedLocationBox) selectedLocationBox.scrollIntoView({ behavior: "smooth" });
        return;
    }

    const roadName = document.getElementById("road-name").value.trim();
    const damageType = document.getElementById("damage-type").value;
    const severity = document.getElementById("severity").value;
    const description = document.getElementById("description").value.trim();

    if (!roadName) {
        reportMessage.textContent = "Please enter the road name.";
        reportMessage.className = "message-error";
        return;
    }

    if (!damageType) {
        reportMessage.textContent = "Please select a damage type.";
        reportMessage.className = "message-error";
        return;
    }

    if (!severity) {
        reportMessage.textContent = "Please select a severity level.";
        reportMessage.className = "message-error";
        return;
    }

    // Determine photo to send
    let photoToSend = null;
    if (damagePhotoInput && damagePhotoInput.files && damagePhotoInput.files[0]) {
        photoToSend = damagePhotoInput.files[0];
    } else if (stagedPhotoFile) {
        photoToSend = stagedPhotoFile;
    }

    if (photoToSend) {
        if (!photoToSend.name.toLowerCase().match(/\.(jpe?g)$/i) || photoToSend.size > 10 * 1024 * 1024) {
            reportMessage.textContent = "Attached photograph must be a JPEG under 10 MB.";
            reportMessage.className = "message-error";
            return;
        }
    }

    // Prepare FormData using strictly selected coordinates
    const formData = new FormData();
    formData.append("road_name", roadName);
    formData.append("damage_type", damageType);
    formData.append("severity", severity);
    formData.append("description", description);
    formData.append("latitude", selectedCoordinates.lat.toFixed(6));
    formData.append("longitude", selectedCoordinates.lng.toFixed(6));
    if (photoToSend) {
        formData.append("photo", photoToSend);
    }

    if (submitReportBtn) submitReportBtn.disabled = true;
    reportMessage.textContent = "Submitting road-damage report...";
    reportMessage.className = "message-info";

    try {
        const response = await fetch("/api/reports", {
            method: "POST",
            body: formData
        });

        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            const errText = await response.text();
            console.error("Non-JSON response from /api/reports:", response.status, errText);
            throw new Error(`Server returned a non-JSON response (HTTP ${response.status}).`);
        }

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Failed to submit road-damage report.");
        }

        const report = data.report;
        submittedReports.push(report);

        // Place submitted report marker on the map
        const reportMarker = L.circleMarker([report.latitude, report.longitude], {
            radius: 9,
            color: "#ffffff",
            weight: 2,
            fillColor: severityColors[report.severity] || "#64748b",
            fillOpacity: 1
        }).addTo(map);

        reportMarkers[report.id] = reportMarker;

        let popupContent = `
            <strong>Submitted Report #${report.id}</strong><br>
            <strong>Road:</strong> ${escapeHTML(report.road_name)}<br>
            <strong>Damage:</strong> ${escapeHTML(report.damage_type)}<br>
            <strong>Severity:</strong> <span style="color:${severityColors[report.severity]};font-weight:bold;">${escapeHTML(report.severity)}</span><br>
            <strong>Coordinates:</strong> ${report.latitude.toFixed(6)}, ${report.longitude.toFixed(6)}<br>
            <strong>Reported:</strong> ${escapeHTML(report.created_at)}<br>
        `;

        if (report.description) {
            popupContent += `<strong>Description:</strong> ${escapeHTML(report.description)}<br>`;
        }

        if (report.photo_url) {
            popupContent += `
                <div style="margin-top:6px;">
                    <a href="${report.photo_url}" target="_blank">
                        <img src="${report.photo_url}" alt="Report photo" style="width:100%;max-width:200px;height:110px;max-height:110px;object-fit:cover;border-radius:4px;display:block;">
                    </a>
                </div>
            `;
        }

        popupContent += `<em style="color:#64748b;font-size:11px;">Saved to PostgreSQL/PostGIS.</em>`;

        reportMarker.bindPopup(popupContent).openPopup();
        map.setView([report.latitude, report.longitude], Math.max(map.getZoom(), 16));

        // Render card in the Submitted Reports Feed
        renderReportCard(report);

        // Display verified success message
        reportMessage.textContent =
            `Report #${report.id} submitted successfully! Placed at ${report.latitude.toFixed(6)}, ${report.longitude.toFixed(6)}. View it on the map above or in the reports feed below.`;
        reportMessage.className = "message-success";

        // Reset the form fields and staging state
        damageForm.reset();
        stagedPhotoFile = null;
        selectedCoordinates = null;
        clearAiDetection();
        clearMapAnalysis();
        if (roadNameAiBadge) roadNameAiBadge.style.display = "none";

        if (selectedLocation) {
            selectedLocation.textContent = "No location selected yet. Click the map or upload a geotagged photo.";
        }
        if (selectedLocationBox) {
            selectedLocationBox.classList.remove("selected");
        }
        if (damagePhotoPreview) {
            damagePhotoPreview.hidden = true;
            damagePhotoPreview.style.display = "none";
            damagePhotoPreview.removeAttribute("src");
        }
        if (autoFillGpsBtn) {
            autoFillGpsBtn.style.display = "none";
        }
        if (window.selectionMarker) {
            map.removeLayer(window.selectionMarker);
            window.selectionMarker = null;
        }

        // Smoothly scroll up to the map so the user immediately sees the pinned marker
        const mapPanel = document.querySelector(".map-panel");
        if (mapPanel) {
            mapPanel.scrollIntoView({ behavior: "smooth", block: "start" });
        }

    } catch (err) {
        console.error("Report submission failed:", err);
        reportMessage.textContent = err.message || "Failed to submit road-damage report.";
        reportMessage.className = "message-error";
    } finally {
        if (submitReportBtn) submitReportBtn.disabled = false;
    }
});

// ============================================================
// Stage 5: GIS Analytics, Filters, Hotspots & Prioritisation
// ============================================================

let currentFilters = {
    date_from: "",
    date_to: "",
    damage_type: "",
    severity: ""
};

let activeMapMode = "markers"; // "markers" | "hotspots" | "traffic"
const hotspotLayerGroup = L.layerGroup().addTo(map);
let clientConfig = null;
let googleMapInstance = null;
let googleTrafficLayerInstance = null;

// DOM Elements: Stage 5 Controls
const filterDateFromInput = document.getElementById("filter-date-from");
const filterDateToInput = document.getElementById("filter-date-to");
const filterDamageTypeSelect = document.getElementById("filter-damage-type");
const filterSeveritySelect = document.getElementById("filter-severity");
const applyFiltersBtn = document.getElementById("apply-filters-btn");
const resetFiltersBtn = document.getElementById("reset-filters-btn");
const recomputePriorityBtn = document.getElementById("recompute-priority-btn");

const btnViewMarkers = document.getElementById("btn-view-markers");
const btnViewHotspots = document.getElementById("btn-view-hotspots");
const btnViewTraffic = document.getElementById("btn-view-traffic");
const mapModeIndicator = document.getElementById("map-mode-indicator");
const hotspotBanner = document.getElementById("hotspot-banner");

const leafletMapDiv = document.getElementById("map");
const googleTrafficContainer = document.getElementById("google-traffic-container");
const googleTrafficMapDiv = document.getElementById("google-traffic-map");
const googleTrafficFallback = document.getElementById("google-traffic-fallback");

// Summary KPI elements
const kpiTotalReports = document.getElementById("kpi-total-reports");
const kpiFilterStatus = document.getElementById("kpi-filter-status");
const kpiSevCrit = document.getElementById("kpi-sev-crit");
const kpiSevMod = document.getElementById("kpi-sev-mod");
const kpiSevMin = document.getElementById("kpi-sev-min");
const kpiPrioHigh = document.getElementById("kpi-prio-high");
const kpiPrioMed = document.getElementById("kpi-prio-med");
const kpiPrioLow = document.getElementById("kpi-prio-low");
const kpiDamageTypes = document.getElementById("kpi-damage-types");

const extDataRoads = document.getElementById("ext-data-roads");
const extDataJunctions = document.getElementById("ext-data-junctions");
const extDataTraffic = document.getElementById("ext-data-traffic");

/**
 * Fetch client-safe configuration (Google Maps API key status, default study area, etc.)
 */
async function fetchClientConfig() {
    try {
        const res = await fetch("/api/config");
        if (res.ok) {
            clientConfig = await res.json();
        }
    } catch (err) {
        console.warn("Could not load client config:", err);
    }
}

/**
 * Fetch external GIS dataset status and update UI provenance badges.
 */
async function fetchExternalDataStatus() {
    try {
        const res = await fetch("/api/analytics/external-data-status");
        if (!res.ok) return;
        const data = await res.json();
        const status = data.status_data || {};

        if (extDataRoads && status.road_segments) {
            const count = status.road_segments.count;
            extDataRoads.textContent = `Road Network: ${count} segments`;
            extDataRoads.className = `badge ${count > 0 ? "badge-auto" : "badge-manual"}`;
        }
        if (extDataJunctions && status.junctions) {
            const count = status.junctions.count;
            extDataJunctions.textContent = `Junctions: ${count} points`;
            extDataJunctions.className = `badge ${count > 0 ? "badge-auto" : "badge-manual"}`;
        }
        if (extDataTraffic && status.traffic_counts) {
            const count = status.traffic_counts.count;
            extDataTraffic.textContent = `Traffic (AADT): ${count} stations`;
            extDataTraffic.className = `badge ${count > 0 ? "badge-auto" : "badge-manual"}`;
        }
    } catch (err) {
        console.warn("Could not fetch external GIS dataset status:", err);
    }
}

/**
 * Update the Summary KPI cards with latest aggregated counts.
 */
function updateSummaryUI(summary, isFiltered) {
    if (!summary) return;

    if (kpiTotalReports) {
        kpiTotalReports.textContent = summary.total || 0;
    }
    if (kpiFilterStatus) {
        kpiFilterStatus.textContent = isFiltered ? "Filtered results" : "All reports in database";
    }

    const sev = summary.by_severity || {};
    if (kpiSevCrit) kpiSevCrit.textContent = `Critical: ${sev.Critical || 0}`;
    if (kpiSevMod) kpiSevMod.textContent = `Moderate: ${sev.Moderate || 0}`;
    if (kpiSevMin) kpiSevMin.textContent = `Minor: ${sev.Minor || 0}`;

    const prio = summary.by_priority_tier || {};
    if (kpiPrioHigh) kpiPrioHigh.textContent = `High: ${prio.High || 0}`;
    if (kpiPrioMed) kpiPrioMed.textContent = `Medium: ${prio.Medium || 0}`;
    if (kpiPrioLow) kpiPrioLow.textContent = `Low: ${prio.Low || 0}`;

    if (kpiDamageTypes) {
        const types = summary.by_damage_type || {};
        const entries = Object.entries(types);
        if (entries.length === 0) {
            kpiDamageTypes.innerHTML = "<span>No incident types found</span>";
        } else {
            kpiDamageTypes.innerHTML = entries
                .map(([name, count]) => `<span>• <strong>${escapeHTML(name)}:</strong> ${count}</span>`)
                .join("");
        }
    }
}

/**
 * Update Leaflet map markers to match the current filtered reports.
 */
function updateMapMarkers(reports) {
    const visibleIds = new Set(reports.map(r => r.id));

    // First ensure every report has a Leaflet circle marker created
    reports.forEach(report => {
        if (!reportMarkers[report.id]) {
            const marker = L.circleMarker([report.latitude, report.longitude], {
                radius: 8,
                color: "#ffffff",
                weight: 2,
                fillColor: severityColors[report.severity] || "#64748b",
                fillOpacity: 0.95
            });

            const tierClass = report.priority_tier === "High" ? "priority-high" : (report.priority_tier === "Medium" ? "priority-med" : "priority-low");
            let popup = `
                <strong>Report #${report.id}</strong><br>
                <strong>Road:</strong> ${escapeHTML(report.road_name)}<br>
                <strong>Damage:</strong> ${escapeHTML(report.damage_type)}<br>
                <strong>Severity:</strong> <span style="color:${severityColors[report.severity]};font-weight:bold;">${escapeHTML(report.severity)}</span><br>
            `;
            if (report.priority_tier) {
                popup += `<strong>Priority:</strong> <span class="priority-tier-badge ${tierClass}">${escapeHTML(report.priority_tier)}</span> (${report.priority_score} pts)<br>`;
            }
            popup += `
                <strong>Coordinates:</strong> ${report.latitude.toFixed(6)}, ${report.longitude.toFixed(6)}<br>
                <strong>Reported:</strong> ${escapeHTML(report.created_at)}<br>
            `;
            if (report.description) {
                popup += `<strong>Description:</strong> ${escapeHTML(report.description)}<br>`;
            }
            if (report.photo_url) {
                popup += `<div style="margin-top:6px;"><a href="${report.photo_url}" target="_blank"><img src="${report.photo_url}" style="width:100%;max-width:200px;height:110px;max-height:110px;object-fit:cover;border-radius:4px;display:block;"></a></div>`;
            }
            marker.bindPopup(popup);
            reportMarkers[report.id] = marker;
        }
    });

    // Control visibility based on active mode and filter set
    Object.keys(reportMarkers).forEach(idStr => {
        const id = parseInt(idStr, 10);
        const marker = reportMarkers[id];
        if (activeMapMode === "markers" && visibleIds.has(id)) {
            if (!map.hasLayer(marker)) {
                marker.addTo(map);
            }
        } else {
            if (map.hasLayer(marker)) {
                map.removeLayer(marker);
            }
        }
    });
}

/**
 * Load reports and summary counts matching current filter criteria.
 */
async function loadFilteredData(filters = currentFilters) {
    const params = new URLSearchParams();
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    if (filters.damage_type) params.set("damage_type", filters.damage_type);
    if (filters.severity) params.set("severity", filters.severity);

    const isFiltered = Boolean(filters.date_from || filters.date_to || filters.damage_type || filters.severity);
    const queryString = params.toString() ? `?${params.toString()}` : "";

    try {
        const [reportsRes, summaryRes] = await Promise.all([
            fetch(`/api/reports${queryString}`),
            fetch(`/api/analytics/summary${queryString}`)
        ]);

        if (!reportsRes.ok) {
            let errorMsg = "Could not load reports from database.";
            try {
                const errData = await reportsRes.json();
                if (errData && errData.error) errorMsg = errData.error;
            } catch (_) {}
            throw new Error(errorMsg);
        }

        const reportsData = await reportsRes.json();
        const reports = reportsData.reports || [];

        // Clear submittedReports and re-populate
        submittedReports.length = 0;
        reports.forEach(r => submittedReports.push(r));

        // Re-render report cards in feed
        const reportsList = document.getElementById("reports-list");
        if (reportsList) {
            reportsList.innerHTML = "";
            if (reports.length === 0) {
                reportsList.innerHTML = `
                    <p id="no-reports-msg" style="color: #64748b; font-style: italic; padding: 12px 0;">
                        No road damage reports match the selected filter criteria.
                    </p>
                `;
            } else {
                // Render from first to last (renderReportCard uses prepend)
                reports.forEach(r => renderReportCard(r));
            }
        }

        const reportsCountBadge = document.getElementById("reports-count-badge");
        if (reportsCountBadge) {
            reportsCountBadge.textContent = `${reports.length} ${reports.length === 1 ? "report" : "reports"}`;
        }

        // Update map markers
        updateMapMarkers(reports);

        // Update Summary KPI Cards
        if (summaryRes.ok) {
            const summaryData = await summaryRes.json();
            updateSummaryUI(summaryData.summary, isFiltered);
        }

        // If in hotspot mode, refresh hotspot clusters
        if (activeMapMode === "hotspots") {
            await fetchAndRenderHotspots();
        }

    } catch (err) {
        console.warn("Error loading filtered reports:", err);
        const noReportsMsg = document.getElementById("no-reports-msg");
        if (noReportsMsg) {
            noReportsMsg.textContent = "Could not load reports from database: " + err.message;
            noReportsMsg.style.color = "#dc2626";
        }
        if (reportMessage) {
            reportMessage.textContent = "Database notice: " + err.message;
            reportMessage.className = "message-error";
        }
    }
}

/**
 * Fetch and render PostGIS DBSCAN Hotspot Clusters on the Leaflet map.
 */
async function fetchAndRenderHotspots() {
    hotspotLayerGroup.clearLayers();
    if (!hotspotBanner) return;

    try {
        const res = await fetch("/api/analytics/hotspots?min_reports=3");
        if (!res.ok) {
            hotspotBanner.style.display = "block";
            hotspotBanner.className = "hotspot-notice-banner hotspot-banner-warning";
            hotspotBanner.innerHTML = "<span>⚠️ Could not compute hotspot clusters from PostGIS.</span>";
            return;
        }

        const body = await res.json();
        const data = body.data || {};

        hotspotBanner.style.display = "flex";

        if (!data.threshold_met) {
            hotspotBanner.className = "hotspot-notice-banner hotspot-banner-warning";
            hotspotBanner.innerHTML = `
                <div>
                    <strong>⚠️ Density Threshold Not Reached:</strong>
                    ${escapeHTML(data.message || "")}
                    (Total: ${data.total_reports || 0} / ${data.min_reports || 3} required)
                </div>
            `;
            return;
        }

        const clusters = data.clusters || [];
        hotspotBanner.className = "hotspot-notice-banner hotspot-banner-info";
        hotspotBanner.innerHTML = `
            <div>
                <strong>🔥 PostGIS DBSCAN Hotspots:</strong>
                ${clusters.length} density clusters identified across ${data.included_reports} incident locations (SRID 32643 UTM 43N projected metric distance).
            </div>
            <span style="font-size:12px; color:#1e40af;">Radius: 300 m · Min: ${data.min_reports} reports</span>
        `;

        clusters.forEach((c, idx) => {
            const sevColor = severityColors[c.dominant_severity] || "#dc2626";

            // Visual density circle in projected metre radius
            const circle = L.circle([c.centroid_lat, c.centroid_lng], {
                radius: Math.min(450, 140 + c.count * 40),
                color: sevColor,
                fillColor: sevColor,
                fillOpacity: 0.35,
                weight: 2
            }).addTo(hotspotLayerGroup);

            // Centroid count badge
            const icon = L.divIcon({
                className: "cluster-bubble",
                html: `<div style="background:${sevColor}; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; font-weight:800; font-size:13px; border:2px solid #fff; box-shadow:0 2px 8px rgba(0,0,0,0.35);">${c.count}</div>`,
                iconSize: [36, 36],
                iconAnchor: [18, 18]
            });

            const marker = L.marker([c.centroid_lat, c.centroid_lng], { icon: icon }).addTo(hotspotLayerGroup);

            const popup = `
                <strong>Hotspot Cluster #${idx + 1}</strong><br>
                <strong>Incident count:</strong> ${c.count} reports<br>
                <strong>Dominant severity:</strong> <span style="color:${sevColor};font-weight:bold;">${escapeHTML(c.dominant_severity)}</span><br>
                <strong>Dominant damage:</strong> ${escapeHTML(c.dominant_damage_type)}<br>
                <strong>Report IDs:</strong> ${c.report_ids.join(", ")}<br>
                <small style="color:#64748b;font-size:11px;">Algorithm: PostGIS ST_ClusterDBSCAN in UTM 43N (SRID 32643).</small>
            `;
            circle.bindPopup(popup);
            marker.bindPopup(popup);
        });

    } catch (err) {
        console.warn("Hotspot clustering error:", err);
        hotspotBanner.style.display = "block";
        hotspotBanner.className = "hotspot-notice-banner hotspot-banner-warning";
        hotspotBanner.innerHTML = `<span>⚠️ Hotspot analysis failed: ${escapeHTML(err.message)}</span>`;
    }
}

const btnViewRepaired = document.getElementById("btn-view-repaired");
const repairedLayerGroup = L.layerGroup().addTo(map);

/**
 * Initialize or switch to the requested map view mode.
 */
function switchMapMode(newMode) {
    activeMapMode = newMode;

    [btnViewMarkers, btnViewRepaired, btnViewHotspots, btnViewTraffic].forEach(btn => {
        if (btn) btn.classList.remove("active");
    });

    if (newMode === "markers") {
        if (btnViewMarkers) btnViewMarkers.classList.add("active");
        if (mapModeIndicator) mapModeIndicator.textContent = "Layer: Individual Report Markers (Leaflet OSM)";
        if (hotspotBanner) hotspotBanner.style.display = "none";
        hotspotLayerGroup.clearLayers();
        repairedLayerGroup.clearLayers();

        if (leafletMapDiv) leafletMapDiv.style.display = "block";
        if (googleTrafficContainer) googleTrafficContainer.style.display = "none";

        map.invalidateSize();
        updateMapMarkers(submittedReports);

    } else if (newMode === "repaired") {
        if (btnViewRepaired) btnViewRepaired.classList.add("active");
        if (mapModeIndicator) mapModeIndicator.textContent = "Layer: Verified Road Repairs & Completed Maintenance (Leaflet OSM)";
        if (hotspotBanner) hotspotBanner.style.display = "none";
        hotspotLayerGroup.clearLayers();

        if (leafletMapDiv) leafletMapDiv.style.display = "block";
        if (googleTrafficContainer) googleTrafficContainer.style.display = "none";

        // Remove individual unverified markers from view to highlight verified repairs
        Object.values(reportMarkers).forEach(m => {
            if (map.hasLayer(m)) map.removeLayer(m);
        });

        map.invalidateSize();
        fetchAndRenderRepairedRoads();

    } else if (newMode === "hotspots") {
        if (btnViewHotspots) btnViewHotspots.classList.add("active");
        if (mapModeIndicator) mapModeIndicator.textContent = "Layer: PostGIS DBSCAN Hotspot Density Clusters (SRID 32643)";
        repairedLayerGroup.clearLayers();

        if (leafletMapDiv) leafletMapDiv.style.display = "block";
        if (googleTrafficContainer) googleTrafficContainer.style.display = "none";

        // Remove individual markers from view to highlight clusters
        Object.values(reportMarkers).forEach(m => {
            if (map.hasLayer(m)) map.removeLayer(m);
        });

        map.invalidateSize();
        fetchAndRenderHotspots();

    } else if (newMode === "traffic") {
        if (btnViewTraffic) btnViewTraffic.classList.add("active");
        if (mapModeIndicator) mapModeIndicator.textContent = "Layer: Google Live Traffic (Visual Context Only)";
        if (hotspotBanner) hotspotBanner.style.display = "none";
        repairedLayerGroup.clearLayers();

        if (leafletMapDiv) leafletMapDiv.style.display = "none";
        if (googleTrafficContainer) googleTrafficContainer.style.display = "block";

        renderGoogleTrafficView();
    }
}

/**
 * Fetch and render verified completed repairs on the Leaflet map.
 */
async function fetchAndRenderRepairedRoads() {
    repairedLayerGroup.clearLayers();
    try {
        const res = await fetch("/api/public/progress?status=Completed&limit=100");
        if (!res.ok) return;
        const data = await res.json();
        const updates = data.updates || [];

        if (updates.length === 0) {
            if (mapMessage) mapMessage.textContent = "No completed repairs recorded yet.";
            return;
        }

        updates.forEach((up, idx) => {
            if (!up.latitude || !up.longitude) return;

            const marker = L.circleMarker([up.latitude, up.longitude], {
                radius: 9,
                color: "#ffffff",
                weight: 2,
                fillColor: "#059669",
                fillOpacity: 0.95
            }).addTo(repairedLayerGroup);

            let photoHtml = "";
            if (up.attachments && up.attachments.length > 0) {
                const imgAtt = up.attachments.find(a => a.file_type === "image");
                if (imgAtt) {
                    photoHtml = `
                        <div style="margin-top:8px;">
                            <a href="${imgAtt.download_url}" target="_blank" title="View full maintenance-completed photograph">
                                <img src="${imgAtt.download_url}" alt="Completed Repair Photo" style="width:100%; max-height:140px; object-fit:cover; border-radius:6px; border:1px solid #cbd5e1;">
                            </a>
                            <small style="color:#059669; font-weight:600; display:block; margin-top:3px;">📷 Maintenance-Completed Photograph</small>
                        </div>
                    `;
                }
            }

            const popupContent = `
                <div style="font-family: inherit; font-size: 12.5px; max-width: 260px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; margin-bottom:4px;">
                        <strong>🛣️ ${escapeHTML(up.road_name)}</strong>
                        <span class="badge badge-completed" style="font-size:10.5px;">Completed</span>
                    </div>
                    <div style="font-size:11.5px; color:#64748b; margin-bottom:4px;">
                        Work Order: <code>${escapeHTML(up.work_order_code)}</code> &bull; Repaired: ${escapeHTML(up.created_at ? up.created_at.slice(0, 10) : '')}
                    </div>
                    <div style="color:#1e293b; line-height:1.4;">
                        <strong>Work Executed:</strong> ${escapeHTML(up.work_completed)}
                    </div>
                    ${photoHtml}
                </div>
            `;
            marker.bindPopup(popupContent);
        });

    } catch (err) {
        console.warn("Could not load repaired roads layer:", err);
    }
}

if (btnViewRepaired) {
    btnViewRepaired.addEventListener("click", () => switchMapMode("repaired"));
}

/**
 * Handle Google Live Traffic Map rendering or fallback display.
 */
function renderGoogleTrafficView() {
    if (!clientConfig) {
        fetchClientConfig().then(() => renderGoogleTrafficView());
        return;
    }

    const hasKey = Boolean(clientConfig.google_maps_configured && clientConfig.google_maps_api_key);

    if (!hasKey) {
        if (googleTrafficMapDiv) googleTrafficMapDiv.style.display = "none";
        if (googleTrafficFallback) googleTrafficFallback.style.display = "block";
        return;
    }

    if (googleTrafficFallback) googleTrafficFallback.style.display = "none";
    if (googleTrafficMapDiv) googleTrafficMapDiv.style.display = "block";

    if (window.google && window.google.maps) {
        initGoogleTrafficMap();
    } else {
        // Load Google Maps JavaScript API dynamically
        const scriptId = "google-maps-js-sdk";
        if (!document.getElementById(scriptId)) {
            const script = document.createElement("script");
            script.id = scriptId;
            script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(clientConfig.google_maps_api_key)}&callback=initGoogleTrafficMap`;
            script.async = true;
            script.defer = true;
            document.head.appendChild(script);
        }
    }
}

window.initGoogleTrafficMap = function () {
    if (!googleTrafficMapDiv) return;
    try {
        const center = (clientConfig && clientConfig.study_area)
            ? { lat: clientConfig.study_area.latitude, lng: clientConfig.study_area.longitude }
            : { lat: 8.5241, lng: 76.9366 };

        if (!googleMapInstance) {
            googleMapInstance = new google.maps.Map(googleTrafficMapDiv, {
                center: center,
                zoom: 13,
                mapTypeId: "roadmap",
                streetViewControl: false,
                fullscreenControl: true,
            });

            googleTrafficLayerInstance = new google.maps.TrafficLayer();
            googleTrafficLayerInstance.setMap(googleMapInstance);
        } else {
            google.maps.event.trigger(googleMapInstance, "resize");
        }
    } catch (err) {
        console.warn("Could not initialize Google Traffic Map:", err);
        if (googleTrafficFallback) googleTrafficFallback.style.display = "block";
        if (googleTrafficMapDiv) googleTrafficMapDiv.style.display = "none";
    }
};

// Event Listeners: Filter actions
if (applyFiltersBtn) {
    applyFiltersBtn.addEventListener("click", () => {
        currentFilters = {
            date_from: (filterDateFromInput && filterDateFromInput.value) || "",
            date_to: (filterDateToInput && filterDateToInput.value) || "",
            damage_type: (filterDamageTypeSelect && filterDamageTypeSelect.value) || "",
            severity: (filterSeveritySelect && filterSeveritySelect.value) || ""
        };
        loadFilteredData(currentFilters);
    });
}

if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
        if (filterDateFromInput) filterDateFromInput.value = "";
        if (filterDateToInput) filterDateToInput.value = "";
        if (filterDamageTypeSelect) filterDamageTypeSelect.value = "";
        if (filterSeveritySelect) filterSeveritySelect.value = "";

        currentFilters = { date_from: "", date_to: "", damage_type: "", severity: "" };
        loadFilteredData(currentFilters);
    });
}

// Event Listeners: Recompute priority scores
if (recomputePriorityBtn) {
    recomputePriorityBtn.addEventListener("click", async () => {
        recomputePriorityBtn.disabled = true;
        recomputePriorityBtn.textContent = "Computing...";
        try {
            const res = await fetch("/api/analytics/priority/compute", { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                alert(`Success: ${data.message || "Priority scores updated."}`);
                await loadFilteredData(currentFilters);
            } else {
                alert(`Error: ${data.error || "Failed to recompute priorities."}`);
            }
        } catch (err) {
            alert(`Network error: ${err.message}`);
        } finally {
            recomputePriorityBtn.disabled = false;
            recomputePriorityBtn.textContent = "⚡ Recalculate Priorities";
        }
    });
}

// Event Listeners: Map Mode Switcher
if (btnViewMarkers) {
    btnViewMarkers.addEventListener("click", () => switchMapMode("markers"));
}
if (btnViewHotspots) {
    btnViewHotspots.addEventListener("click", () => switchMapMode("hotspots"));
}
if (btnViewTraffic) {
    btnViewTraffic.addEventListener("click", () => switchMapMode("traffic"));
}

// Initial bootstrap of Stage 5 data
(async function initStage5() {
    await fetchClientConfig();
    await fetchExternalDataStatus();
    await loadFilteredData(currentFilters);
    mapMessage.textContent =
        "Map ready. Click anywhere on the map to select an incident location, or use the photo tool to extract GPS.";
})();


// ============================================================
// Stage 6A: Maintenance Work-Order Export
// ============================================================

// Track report IDs the user has checked for selective export
const selectedExportIds = new Set();

/**
 * Update the "Export selected" group visibility and badge count.
 */
function updateExportSelectionUI() {
    const count = selectedExportIds.size;
    const badge = document.getElementById("export-selection-badge");
    const group = document.getElementById("export-selected-group");

    if (badge) {
        badge.textContent = `${count} selected`;
        badge.style.display = count > 0 ? "inline-block" : "none";
    }
    if (group) {
        group.style.display = count > 0 ? "flex" : "none";
    }
}

/**
 * Inject a selection checkbox into an existing report card DOM element.
 * Called from renderReportCard after the card is created.
 */
function injectExportCheckbox(card, reportId) {
    if (!card || card.querySelector(".report-select-checkbox")) return;

    const wrap = document.createElement("div");
    wrap.className = "report-select-wrap";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "report-select-checkbox";
    cb.id = `select-report-${reportId}`;
    cb.setAttribute("aria-label", `Select Report #${reportId} for export`);
    cb.checked = selectedExportIds.has(reportId);

    cb.addEventListener("change", () => {
        if (cb.checked) {
            selectedExportIds.add(reportId);
            card.classList.add("selected-for-export");
        } else {
            selectedExportIds.delete(reportId);
            card.classList.remove("selected-for-export");
            // Uncheck "select all" if one is deselected
            const selectAll = document.getElementById("select-all-reports");
            if (selectAll) selectAll.checked = false;
        }
        updateExportSelectionUI();
    });

    const lbl = document.createElement("label");
    lbl.htmlFor = `select-report-${reportId}`;
    lbl.style.cssText = "font-size:11.5px; color:#64748b; cursor:pointer;";
    lbl.textContent = `Include in export`;

    wrap.appendChild(cb);
    wrap.appendChild(lbl);

    // Insert before the first child of the card
    card.insertBefore(wrap, card.firstChild);
}

// "Select all" checkbox in the reports heading
const selectAllCheckbox = document.getElementById("select-all-reports");
if (selectAllCheckbox) {
    selectAllCheckbox.addEventListener("change", () => {
        const isChecked = selectAllCheckbox.checked;
        document.querySelectorAll(".report-select-checkbox").forEach(cb => {
            const idStr = cb.id.replace("select-report-", "");
            const rid = parseInt(idStr, 10);
            if (!isNaN(rid)) {
                cb.checked = isChecked;
                const card = document.getElementById(`report-card-${rid}`);
                if (isChecked) {
                    selectedExportIds.add(rid);
                    if (card) card.classList.add("selected-for-export");
                } else {
                    selectedExportIds.delete(rid);
                    if (card) card.classList.remove("selected-for-export");
                }
            }
        });
        updateExportSelectionUI();
    });
}

/**
 * Build the export URL from active filters and an optional list of report IDs.
 * Never includes DATABASE_URL or credentials.
 */
function buildExportUrl(format, useSelectedIds) {
    const base = `/api/export/${format}`;
    const params = new URLSearchParams();

    if (useSelectedIds && selectedExportIds.size > 0) {
        params.set("report_ids", Array.from(selectedExportIds).join(","));
    } else {
        // Mirror currently active filters
        if (currentFilters.date_from)   params.set("date_from",   currentFilters.date_from);
        if (currentFilters.date_to)     params.set("date_to",     currentFilters.date_to);
        if (currentFilters.damage_type) params.set("damage_type", currentFilters.damage_type);
        if (currentFilters.severity)    params.set("severity",    currentFilters.severity);
    }

    return params.toString() ? `${base}?${params.toString()}` : base;
}

/**
 * Trigger a file download by creating a temporary <a> and clicking it.
 * Handles both success (blob download) and server-side error messages.
 */
async function triggerExportDownload(format, useSelectedIds) {
    const msgEl = document.getElementById("export-message");

    const setMsg = (text, cls) => {
        if (!msgEl) return;
        msgEl.textContent = text;
        msgEl.className = cls;
    };

    // Determine which button set to disable
    const suffix = useSelectedIds ? "selected" : "filtered";
    const pdfBtn = document.getElementById(`btn-export-pdf-${suffix}`);
    const csvBtn = document.getElementById(`btn-export-csv-${suffix}`);
    const geoBtn = document.getElementById(`btn-export-geojson-${suffix}`);
    const allBtns = [pdfBtn, csvBtn, geoBtn].filter(Boolean);

    allBtns.forEach(b => { b.disabled = true; });
    setMsg(`Generating ${format.toUpperCase()} export…`, "export-info");

    const url = buildExportUrl(format, useSelectedIds);

    try {
        const res = await fetch(url);

        // Handle server error JSON responses
        if (!res.ok) {
            let errMsg = `Server error ${res.status}.`;
            const ct = res.headers.get("content-type") || "";
            if (ct.includes("application/json")) {
                const data = await res.json();
                if (res.status === 404) {
                    setMsg(
                        data.message || "No reports matched the requested criteria.",
                        "export-error"
                    );
                    return;
                }
                errMsg = data.error || errMsg;
            }
            setMsg(`Export failed: ${errMsg}`, "export-error");
            return;
        }

        // Derive filename from Content-Disposition header
        const cd = res.headers.get("content-disposition") || "";
        let filename = `roadwise_workorder.${format}`;
        const fnMatch = cd.match(/filename="([^"]+)"/);
        if (fnMatch) filename = fnMatch[1];

        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = blobUrl;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);

        const count = res.headers.get("x-export-count") || "?";
        setMsg(
            `✓ ${format.toUpperCase()} downloaded: ${filename} (${count} report(s)).`,
            "export-success"
        );

    } catch (err) {
        console.error("Export download error:", err);
        setMsg(`Network error: ${err.message}`, "export-error");
    } finally {
        allBtns.forEach(b => { b.disabled = false; });
    }
}

// Wire up the six export buttons
[
    ["btn-export-pdf-filtered",     "pdf",     false],
    ["btn-export-csv-filtered",     "csv",     false],
    ["btn-export-geojson-filtered", "geojson", false],
    ["btn-export-pdf-selected",     "pdf",     true],
    ["btn-export-csv-selected",     "csv",     true],
    ["btn-export-geojson-selected", "geojson", true],
].forEach(([id, fmt, useSelected]) => {
    const btn = document.getElementById(id);
    if (btn) {
        btn.addEventListener("click", () => triggerExportDownload(fmt, useSelected));
    }
});

// ── Patch renderReportCard to inject export checkboxes ──────────────────────
// Wrap the existing renderReportCard so every new card gets a checkbox added.
(function patchRenderReportCard() {
    const original = window.renderReportCard || renderReportCard;

    // Override the function defined earlier in this file by replacing
    // the variable in the closure-shared scope. Because renderReportCard
    // is defined with `function`, we patch it on window as a fallback and
    // also wrap via a post-prepend hook on the reports list.

    // We use a MutationObserver to detect new report cards added to the list
    // and inject the checkbox immediately after insertion.
    const reportsList = document.getElementById("reports-list");
    if (!reportsList) return;

    const observer = new MutationObserver(mutations => {
        mutations.forEach(m => {
            m.addedNodes.forEach(node => {
                if (node.nodeType !== 1) return;
                const idAttr = node.id || "";
                const match = idAttr.match(/^report-card-(\d+)$/);
                if (match) {
                    const rid = parseInt(match[1], 10);
                    injectExportCheckbox(node, rid);
                }
            });
        });
    });

    observer.observe(reportsList, { childList: true });
})();

// ============================================================
// Stage 6B: Maintenance Work-Order Tracking & Lifecycle
// ============================================================

const btnCreateWoSelected = document.getElementById("btn-create-wo-selected");
const woModal = document.getElementById("wo-modal");
const btnCloseWoModal = document.getElementById("btn-close-wo-modal");
const btnCancelWoModal = document.getElementById("btn-cancel-wo-modal");
const woEditForm = document.getElementById("wo-edit-form");
const editWoStatusSelect = document.getElementById("edit-wo-status");
const statusTransitionHint = document.getElementById("status-transition-hint");

const filterWoStatus = document.getElementById("filter-wo-status");
const filterWoPriority = document.getElementById("filter-wo-priority");
const filterWoSearch = document.getElementById("filter-wo-search");
const applyWoFiltersBtn = document.getElementById("apply-wo-filters-btn");
const resetWoFiltersBtn = document.getElementById("reset-wo-filters-btn");
const refreshWoBtn = document.getElementById("refresh-wo-btn");
const woQueueMessage = document.getElementById("wo-queue-message");

// Enforced lifecycle transition map for client-side guidance
const WO_VALID_TRANSITIONS = {
    "Open": ["Assigned", "In Progress", "Deferred", "Completed"],
    "Assigned": ["Open", "In Progress", "Deferred", "Completed"],
    "In Progress": ["Completed", "Deferred", "Assigned", "Open"],
    "Deferred": ["Open", "Assigned", "In Progress"],
    "Completed": ["Open"]
};

let currentWoDetail = null;

/**
 * Handle explicit creation of work orders from checked reports.
 */
if (btnCreateWoSelected) {
    btnCreateWoSelected.addEventListener("click", async () => {
        if (!currentAuthUser) {
            openLoginModal("Municipal staff authentication is required to create work orders.");
            return;
        }

        if (!selectedExportIds || selectedExportIds.size === 0) {
            alert("Please select at least one road-damage report using the checkboxes first.");
            return;
        }

        const reportIds = Array.from(selectedExportIds);
        btnCreateWoSelected.disabled = true;
        btnCreateWoSelected.textContent = "Creating...";

        const expMsg = document.getElementById("export-message");
        if (expMsg) {
            expMsg.textContent = `Creating work orders for ${reportIds.length} report(s)...`;
            expMsg.className = "export-info";
        }

        try {
            const res = await fetch("/api/work-orders", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ report_ids: reportIds }),
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "Failed to create work orders.");
            }

            if (expMsg) {
                expMsg.textContent = `✓ ${data.message}`;
                expMsg.className = "export-success";
            }

            // Refresh the Work Orders queue and filtered reports
            await loadWorkOrdersQueue();
            if (typeof loadFilteredData === "function") {
                await loadFilteredData(currentFilters);
            }

            // Scroll down to the Work Orders queue to review
            const queuePanel = document.getElementById("work-orders-queue-panel");
            if (queuePanel) {
                queuePanel.scrollIntoView({ behavior: "smooth", block: "start" });
            }

        } catch (err) {
            console.error("Create work orders error:", err);
            if (expMsg) {
                expMsg.textContent = `Error creating work orders: ${err.message}`;
                expMsg.className = "export-error";
            }
        } finally {
            btnCreateWoSelected.disabled = false;
            btnCreateWoSelected.textContent = "⚡ Create Work Order(s)";
        }
    });
}

/**
 * Fetch and render the Work Orders Queue with active filters.
 */
async function loadWorkOrdersQueue() {
    const params = new URLSearchParams();
    if (filterWoStatus && filterWoStatus.value) params.set("status", filterWoStatus.value);
    if (filterWoPriority && filterWoPriority.value) params.set("priority", filterWoPriority.value);
    if (filterWoSearch && filterWoSearch.value.trim()) params.set("search", filterWoSearch.value.trim());

    const qs = params.toString() ? `?${params.toString()}` : "";

    try {
        const res = await fetch(`/api/work-orders${qs}`);
        if (!res.ok) {
            throw new Error(`Server returned HTTP ${res.status}`);
        }

        const data = await res.json();
        const wos = data.work_orders || [];
        const summary = data.summary || {};

        // Update KPI summary cards
        const kpiTotal = document.getElementById("kpi-wo-total");
        const kpiOpen = document.getElementById("kpi-wo-open");
        const kpiAssigned = document.getElementById("kpi-wo-assigned");
        const kpiInProgress = document.getElementById("kpi-wo-inprogress");
        const kpiCompleted = document.getElementById("kpi-wo-completed");
        const kpiDeferred = document.getElementById("kpi-wo-deferred");
        const countBadge = document.getElementById("wo-count-badge");

        if (kpiTotal) kpiTotal.textContent = summary.total !== undefined ? summary.total : wos.length;
        if (kpiOpen) kpiOpen.textContent = `Open: ${summary.Open || 0}`;
        if (kpiAssigned) kpiAssigned.textContent = `Assigned: ${summary.Assigned || 0}`;
        if (kpiInProgress) kpiInProgress.textContent = `In Progress: ${summary["In Progress"] || 0}`;
        if (kpiCompleted) kpiCompleted.textContent = `Completed: ${summary.Completed || 0}`;
        if (kpiDeferred) kpiDeferred.textContent = `Deferred: ${summary.Deferred || 0}`;
        if (countBadge) countBadge.textContent = `${wos.length} work order${wos.length === 1 ? "" : "s"}`;

        renderWorkOrdersList(wos);

    } catch (err) {
        console.warn("Could not load work orders queue:", err);
        if (woQueueMessage) {
            woQueueMessage.textContent = `Notice: ${err.message}`;
            woQueueMessage.style.color = "#dc2626";
        }
    }
}

/**
 * Render work order cards in the queue grid.
 */
function renderWorkOrdersList(wos) {
    const list = document.getElementById("work-orders-list");
    if (!list) return;

    list.innerHTML = "";

    if (!wos || wos.length === 0) {
        list.innerHTML = `
            <p id="no-wo-msg" style="color: #64748b; font-style: italic; padding: 12px 0;">
                No maintenance work orders match the current filters.
            </p>
        `;
        return;
    }

    wos.forEach(wo => {
        const card = document.createElement("div");
        card.className = "wo-card";
        card.id = `wo-card-${wo.id}`;

        const statusClass = (wo.status || "").toLowerCase().replace(/\s+/g, "-");
        const rep = wo.linked_report || {};
        const pTier = rep.priority_tier || "Low";
        const pTierClass = pTier === "High" ? "priority-high" : (pTier === "Medium" ? "priority-med" : "priority-low");
        const pScoreStr = rep.priority_score !== null && rep.priority_score !== undefined
            ? Number(rep.priority_score).toFixed(1)
            : "0.0";

        card.innerHTML = `
            <div class="wo-card-header">
                <div class="wo-code-title">
                    <span>📋 ${escapeHTML(wo.work_order_code)}</span>
                    <span class="status-badge status-${statusClass}">${escapeHTML(wo.status)}</span>
                </div>
                <div>
                    <span class="priority-tier-badge ${pTierClass}">${escapeHTML(pTier)} Priority (${pScoreStr})</span>
                </div>
            </div>

            <div style="font-size: 13.5px; font-weight: 600; color: #0f172a;">
                ${escapeHTML(rep.road_name || "Unknown Road")}
            </div>

            <div class="wo-grid-details">
                <div>
                    <div class="label">Damage &amp; Severity</div>
                    <div><strong>${escapeHTML(rep.damage_type || "—")}</strong> &bull; ${escapeHTML(rep.severity || "—")}</div>
                </div>
                <div>
                    <div class="label">Assigned To</div>
                    <div>${escapeHTML(wo.assigned_to || "Unassigned")}</div>
                </div>
                <div>
                    <div class="label">Target Date</div>
                    <div>${escapeHTML(wo.target_date || "Not set")}</div>
                </div>
                <div>
                    <div class="label">Completion Date</div>
                    <div>${escapeHTML(wo.completed_at ? wo.completed_at.replace(" UTC", "") : "Pending")}</div>
                </div>
            </div>

            ${wo.notes ? `<div class="wo-notes-box"><strong>Notes:</strong> ${escapeHTML(wo.notes)}</div>` : ""}

            <div class="wo-actions-row">
                <button type="button" class="btn-wo-action btn-wo-action-primary" onclick="openWorkOrderModal(${wo.id})">
                    ✏️ Update Status / Details
                </button>
                ${rep.latitude && rep.longitude ? `
                    <button type="button" class="btn-wo-action" onclick="focusReportOnMap(${rep.id}, ${rep.latitude}, ${rep.longitude})">
                        📍 View on Map
                    </button>
                ` : ""}
            </div>
        `;

        list.appendChild(card);
    });
}

/**
 * Open work order update dialog, populate fields, and enforce status transitions.
 */
window.openWorkOrderModal = async function(woId) {
    if (!woModal) return;

    if (!currentAuthUser) {
        openLoginModal("Municipal staff authentication is required to modify work orders.");
        return;
    }

    const modalTitle = document.getElementById("modal-wo-title");
    const modalSubtitle = document.getElementById("modal-wo-subtitle");
    const editIdInput = document.getElementById("edit-wo-id");
    const editAssigned = document.getElementById("edit-wo-assigned");
    const editTargetDate = document.getElementById("edit-wo-target-date");
    const editNotes = document.getElementById("edit-wo-notes");
    const editStatusNote = document.getElementById("edit-wo-status-note");
    const modalMsg = document.getElementById("modal-wo-message");
    const summaryCard = document.getElementById("modal-linked-report-card");
    const historyList = document.getElementById("modal-wo-history-list");

    if (modalMsg) modalMsg.textContent = "";
    if (editStatusNote) editStatusNote.value = "";

    try {
        const res = await fetch(`/api/work-orders/${woId}`);
        if (!res.ok) throw new Error("Could not load work order details.");
        const data = await res.json();
        const wo = data.work_order;
        currentWoDetail = wo;

        if (modalTitle) modalTitle.textContent = `Work Order ${wo.work_order_code}`;
        if (modalSubtitle) modalSubtitle.textContent = `Linked to Report #${wo.report_id} · Created ${wo.created_at}`;
        if (editIdInput) editIdInput.value = wo.id;
        if (editAssigned) editAssigned.value = wo.assigned_to || "";
        if (editTargetDate) editTargetDate.value = wo.target_date || "";
        if (editNotes) editNotes.value = wo.notes || "";

        // Status dropdown: highlight current and allowed next statuses
        if (editWoStatusSelect) {
            editWoStatusSelect.value = wo.status;
            updateStatusDropdownOptions(wo.status);
        }

        // Render linked report summary
        const rep = wo.linked_report || {};
        if (summaryCard) {
            summaryCard.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>Report #${rep.id}: ${escapeHTML(rep.road_name)}</strong>
                    <span class="badge" style="background:#f1f5f9; color:#0f172a;">${escapeHTML(rep.severity)} &bull; ${escapeHTML(rep.damage_type)}</span>
                </div>
                <div style="font-size:12px; color:#475569;">
                    Coordinates: ${rep.latitude ? rep.latitude.toFixed(6) : "—"}, ${rep.longitude ? rep.longitude.toFixed(6) : "—"} &nbsp;&bull;&nbsp;
                    Priority: <strong>${escapeHTML(rep.priority_tier || "Low")}</strong> (${rep.priority_score || 0} pts)
                </div>
                ${rep.photo_url ? `
                    <div style="margin-top:4px;">
                        <a href="${rep.photo_url}" target="_blank" style="font-size:12px; color:#0284c7;">📷 View Attached Photo</a>
                    </div>
                ` : ""}
            `;
        }

        // Render status history timeline
        if (historyList) {
            historyList.innerHTML = "";
            const history = wo.status_history || [];
            if (history.length === 0) {
                historyList.innerHTML = `<span style="color:#94a3b8; font-style:italic;">No status transitions recorded yet.</span>`;
            } else {
                history.forEach(h => {
                    const item = document.createElement("div");
                    item.className = "wo-history-item";
                    const fromText = h.from_status ? `${h.from_status} → ` : "";
                    item.innerHTML = `
                        <div class="wo-history-meta">${escapeHTML(h.changed_at)}</div>
                        <div class="wo-history-status-change">${escapeHTML(fromText + h.to_status)}</div>
                        ${h.note ? `<div class="wo-history-note">${escapeHTML(h.note)}</div>` : ""}
                    `;
                    historyList.appendChild(item);
                });
            }
        }

        // Stage 8: Load updates and populate progress update status options
        if (typeof loadWorkOrderUpdates === "function") {
            await loadWorkOrderUpdates(wo.id);
        }
        if (typeof updateProgressStatusDropdownOptions === "function") {
            updateProgressStatusDropdownOptions(wo.status);
        }

        woModal.style.display = "flex";

    } catch (err) {
        alert("Error loading work order: " + err.message);
    }
};

/**
 * Configure allowed options in the status dropdown based on current status.
 */
function updateStatusDropdownOptions(currentStatus) {
    if (!editWoStatusSelect) return;

    const allowed = WO_VALID_TRANSITIONS[currentStatus] || [];
    const allOptions = Array.from(editWoStatusSelect.options);

    allOptions.forEach(opt => {
        if (opt.value === currentStatus) {
            opt.disabled = false;
            opt.textContent = `${opt.value} (Current)`;
        } else if (allowed.includes(opt.value)) {
            opt.disabled = false;
            opt.textContent = `${opt.value} → [Allowed]`;
        } else {
            opt.disabled = true;
            opt.textContent = `${opt.value} (Invalid transition)`;
        }
    });

    if (statusTransitionHint) {
        statusTransitionHint.textContent = allowed.length > 0
            ? `Allowed next statuses from ${currentStatus}: ${allowed.join(", ")}.`
            : `Terminal state.`;
    }
}

function closeWorkOrderModal() {
    if (woModal) woModal.style.display = "none";
    currentWoDetail = null;
}

if (btnCloseWoModal) btnCloseWoModal.addEventListener("click", closeWorkOrderModal);
if (btnCancelWoModal) btnCancelWoModal.addEventListener("click", closeWorkOrderModal);

window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && woModal && woModal.style.display !== "none") {
        closeWorkOrderModal();
    }
});

if (woModal) {
    woModal.addEventListener("click", (e) => {
        if (e.target === woModal) closeWorkOrderModal();
    });
}

/**
 * Submit work order changes (status transition and field updates).
 */
if (woEditForm) {
    woEditForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        const woId = document.getElementById("edit-wo-id").value;
        const newStatus = editWoStatusSelect.value;
        const assignedTo = document.getElementById("edit-wo-assigned").value;
        const targetDate = document.getElementById("edit-wo-target-date").value;
        const notes = document.getElementById("edit-wo-notes").value;
        const statusNote = document.getElementById("edit-wo-status-note").value;
        const modalMsg = document.getElementById("modal-wo-message");
        const saveBtn = document.getElementById("btn-save-wo-modal");

        if (saveBtn) saveBtn.disabled = true;
        if (modalMsg) {
            modalMsg.textContent = "Saving changes...";
            modalMsg.style.color = "#0284c7";
        }

        try {
            const res = await fetch(`/api/work-orders/${woId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: newStatus,
                    status_note: statusNote,
                    assigned_to: assignedTo,
                    target_date: targetDate,
                    notes: notes,
                }),
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "Update failed.");
            }

            if (modalMsg) {
                modalMsg.textContent = "✓ Work order updated successfully!";
                modalMsg.style.color = "#059669";
            }

            // Refresh queue and reports feed
            await loadWorkOrdersQueue();
            if (typeof loadFilteredData === "function") {
                await loadFilteredData(currentFilters);
            }

            setTimeout(() => {
                closeWorkOrderModal();
            }, 800);

        } catch (err) {
            console.error("Update work order error:", err);
            if (modalMsg) {
                modalMsg.textContent = `Error: ${err.message}`;
                modalMsg.style.color = "#dc2626";
            }
        } finally {
            if (saveBtn) saveBtn.disabled = false;
        }
    });
}

// Queue filter event handlers
if (applyWoFiltersBtn) {
    applyWoFiltersBtn.addEventListener("click", () => loadWorkOrdersQueue());
}

if (resetWoFiltersBtn) {
    resetWoFiltersBtn.addEventListener("click", () => {
        if (filterWoStatus) filterWoStatus.value = "";
        if (filterWoPriority) filterWoPriority.value = "";
        if (filterWoSearch) filterWoSearch.value = "";
        loadWorkOrdersQueue();
    });
}

if (refreshWoBtn) {
    refreshWoBtn.addEventListener("click", () => loadWorkOrdersQueue());
}

// Initial bootstrap of Stage 6B Queue
(function initStage6B() {
    loadWorkOrdersQueue();
})();

// ============================================================
// Stage 7: Pilot Authentication & Staff Role Management
// ============================================================

let currentAuthUser = null;

const btnOpenLogin = document.getElementById("btn-open-login");
const btnLogout = document.getElementById("btn-logout");
const userProfileBadge = document.getElementById("user-profile-badge");
const userDisplayName = document.getElementById("user-display-name");
const loginModal = document.getElementById("login-modal");
const btnCloseLoginModal = document.getElementById("btn-close-login-modal");
const btnCancelLogin = document.getElementById("btn-cancel-login");
const formLogin = document.getElementById("form-login");
const loginUsernameInput = document.getElementById("login-username");
const loginPasswordInput = document.getElementById("login-password");
const loginErrorAlert = document.getElementById("login-error-alert");

function openLoginModal(notice) {
    if (loginErrorAlert) {
        if (notice) {
            loginErrorAlert.textContent = notice;
            loginErrorAlert.style.display = "block";
        } else {
            loginErrorAlert.style.display = "none";
        }
    }
    if (loginUsernameInput) loginUsernameInput.value = "";
    if (loginPasswordInput) loginPasswordInput.value = "";
    if (loginModal) loginModal.style.display = "flex";
    if (loginUsernameInput) loginUsernameInput.focus();
}

function closeLoginModal() {
    if (loginModal) loginModal.style.display = "none";
}

async function checkAuthStatus() {
    try {
        const res = await fetch("/api/auth/me");
        if (!res.ok) return;
        const data = await res.json();
        if (data.authenticated && data.user) {
            currentAuthUser = data.user;
            if (btnOpenLogin) btnOpenLogin.style.display = "none";
            if (userProfileBadge) {
                userProfileBadge.style.display = "flex";
                if (userDisplayName) {
                    userDisplayName.textContent = `👤 ${data.user.username} (${data.user.role})`;
                }
            }
            updateOfficerLayerUI(true, data.user);
        } else {
            currentAuthUser = null;
            if (btnOpenLogin) btnOpenLogin.style.display = "inline-block";
            if (userProfileBadge) userProfileBadge.style.display = "none";
            updateOfficerLayerUI(false, null);
        }
    } catch (err) {
        console.warn("Could not check auth status:", err);
    }
}

if (btnOpenLogin) btnOpenLogin.addEventListener("click", () => openLoginModal());
if (btnCloseLoginModal) btnCloseLoginModal.addEventListener("click", closeLoginModal);
if (btnCancelLogin) btnCancelLogin.addEventListener("click", closeLoginModal);

if (loginModal) {
    loginModal.addEventListener("click", (e) => {
        if (e.target === loginModal) closeLoginModal();
    });
}

if (formLogin) {
    formLogin.addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = loginUsernameInput.value.trim();
        const password = loginPasswordInput.value;
        const submitBtn = document.getElementById("btn-submit-login");

        if (submitBtn) submitBtn.disabled = true;
        if (loginErrorAlert) loginErrorAlert.style.display = "none";

        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password }),
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "Login failed.");
            }

            closeLoginModal();
            await checkAuthStatus();
            alert(`✓ Welcome, ${data.user.username}! Logged in as ${data.user.role}.`);
        } catch (err) {
            if (loginErrorAlert) {
                loginErrorAlert.textContent = err.message;
                loginErrorAlert.style.display = "block";
            }
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });
}

if (btnLogout) {
    btnLogout.addEventListener("click", async () => {
        try {
            await fetch("/api/auth/logout", { method: "POST" });
            currentAuthUser = null;
            await checkAuthStatus();
            alert("✓ Logged out successfully.");
        } catch (err) {
            console.warn("Logout error:", err);
        }
    });
}

// Initial bootstrap of Stage 7 Pilot Authentication
(function initStage7() {
    checkAuthStatus();
})();

// ============================================================
// Stage 8: Public Road-Repair Progress Updates & Officer UI
// ============================================================

const filterPublicStatus = document.getElementById("filter-public-status");
const filterPublicSearch = document.getElementById("filter-public-search");
const applyPublicFiltersBtn = document.getElementById("apply-public-filters-btn");
const resetPublicFiltersBtn = document.getElementById("reset-public-filters-btn");
const refreshPublicProgressBtn = document.getElementById("refresh-public-progress-btn");
const publicProgressCountBadge = document.getElementById("public-progress-count-badge");
const publicProgressList = document.getElementById("public-progress-list");
const noPublicProgressMsg = document.getElementById("no-public-progress-msg");
const btnPublicPrev = document.getElementById("btn-public-prev");
const btnPublicNext = document.getElementById("btn-public-next");
const publicPageIndicator = document.getElementById("public-page-indicator");

// Modal Progress Update Elements
const formPostProgressUpdate = document.getElementById("form-post-progress-update");
const updateWorkCompleted = document.getElementById("update-work-completed");
const updateProgressPct = document.getElementById("update-progress-pct");
const updateStatusSelect = document.getElementById("update-status");
const updateEstCompletion = document.getElementById("update-est-completion");
const updateBlockers = document.getElementById("update-blockers");
const updateNextActions = document.getElementById("update-next-actions");
const updateInternalNotes = document.getElementById("update-internal-notes");
const updateAttachmentInput = document.getElementById("update-attachment");
const updateCaptionInput = document.getElementById("update-caption");
const updateIsPublishedCheckbox = document.getElementById("update-is-published");
const updateIsCorrectionCheckbox = document.getElementById("update-is-correction");
const updateCorrectionForIdSelect = document.getElementById("update-correction-for-id");
const btnSubmitProgressUpdate = document.getElementById("btn-submit-progress-update");
const modalUpdateFormMsg = document.getElementById("modal-update-form-message");
const modalWoUpdatesList = document.getElementById("modal-wo-updates-list");

let publicProgressPage = 1;
const publicProgressLimit = 10;
let totalPublicUpdates = 0;

/**
 * Configure allowed options for status change in progress update form.
 */
function updateProgressStatusDropdownOptions(currentStatus) {
    if (!updateStatusSelect) return;
    const allowed = WO_VALID_TRANSITIONS[currentStatus] || [];
    const allOptions = Array.from(updateStatusSelect.options);

    allOptions.forEach(opt => {
        if (!opt.value) {
            opt.disabled = false;
            opt.textContent = `Keep current status (${currentStatus})`;
        } else if (opt.value === currentStatus) {
            opt.disabled = false;
            opt.textContent = `${opt.value} (No change)`;
        } else if (allowed.includes(opt.value)) {
            opt.disabled = false;
            opt.textContent = `${opt.value} → [Allowed]`;
        } else {
            opt.disabled = true;
            opt.textContent = `${opt.value} (Invalid transition)`;
        }
    });
}

/**
 * Toggle correction dropdown visibility.
 */
if (updateIsCorrectionCheckbox && updateCorrectionForIdSelect) {
    updateIsCorrectionCheckbox.addEventListener("change", () => {
        updateCorrectionForIdSelect.style.display = updateIsCorrectionCheckbox.checked ? "inline-block" : "none";
    });
}

/**
 * Load officer progress updates timeline for a work order.
 */
window.loadWorkOrderUpdates = async function(woId) {
    if (!modalWoUpdatesList) return;

    modalWoUpdatesList.innerHTML = `<span style="color: #94a3b8; font-style: italic; font-size: 12px;">Loading progress updates...</span>`;
    if (modalUpdateFormMsg) modalUpdateFormMsg.textContent = "";

    // Reset correction dropdown
    if (updateCorrectionForIdSelect) {
        updateCorrectionForIdSelect.innerHTML = `<option value="">Select previous update...</option>`;
    }
    if (updateIsCorrectionCheckbox) updateIsCorrectionCheckbox.checked = false;
    if (updateCorrectionForIdSelect) updateCorrectionForIdSelect.style.display = "none";

    try {
        const res = await fetch(`/api/work-orders/${woId}/updates`);
        if (!res.ok) {
            if (res.status === 401) {
                modalWoUpdatesList.innerHTML = `<span style="color: #94a3b8; font-size: 12px;">Log in as staff to view officer progress timeline.</span>`;
                return;
            }
            throw new Error("Could not load updates.");
        }

        const data = await res.json();
        const updates = data.updates || [];

        if (updates.length === 0) {
            modalWoUpdatesList.innerHTML = `<span style="color: #94a3b8; font-style: italic; font-size: 12px;">No progress updates posted yet. Use the form above to record work.</span>`;
            return;
        }

        modalWoUpdatesList.innerHTML = "";

        // Populate correction dropdown
        updates.forEach(u => {
            if (updateCorrectionForIdSelect) {
                const opt = document.createElement("option");
                opt.value = u.id;
                opt.textContent = `Update #${u.id} (${u.status || "Update"}) - ${u.created_at.slice(0, 16)}`;
                updateCorrectionForIdSelect.appendChild(opt);
            }
        });

        // Render each update
        updates.forEach(up => {
            const item = document.createElement("div");
            item.className = "wo-update-item";

            const isPub = up.is_published;
            const pubBadge = isPub
                ? `<span class="badge" style="background: #ecfdf5; color: #047857; font-size: 11px;">🌐 Published</span>`
                : `<span class="badge" style="background: #f1f5f9; color: #64748b; font-size: 11px;">🔒 Draft / Staff Only</span>`;

            const statusBadge = up.status
                ? `<span class="badge ${getStatusBadgeClass(up.status)}" style="font-size: 11px;">${escapeHTML(up.status)}</span>`
                : "";

            const correctionHtml = up.is_correction
                ? `<div style="font-size: 11px; color: #b45309; font-weight: 600; background: #fffbeb; padding: 2px 6px; border-radius: 4px; display: inline-block;">⚠️ Correction for Update #${up.correction_for_id}</div>`
                : "";

            const internalNotesHtml = up.internal_notes
                ? `<div style="font-size: 11.5px; color: #475569; background: #f8fafc; border-left: 2px solid #cbd5e1; padding: 4px 8px; margin-top: 4px;">
                        <strong>🔒 Staff Note:</strong> ${escapeHTML(up.internal_notes)}
                   </div>`
                : "";

            let attachmentsHtml = "";
            if (up.attachments && up.attachments.length > 0) {
                attachmentsHtml = `<div class="attachment-container">` + up.attachments.map(att => {
                    const dlUrl = att.download_url;
                    if (att.file_type === "image") {
                        return `
                            <a href="${dlUrl}" target="_blank" title="${escapeHTML(att.caption || att.original_filename)}" style="display: inline-block;">
                                <img src="${dlUrl}" class="attachment-thumbnail" alt="${escapeHTML(att.caption || 'Photo')}">
                            </a>
                        `;
                    } else {
                        return `
                            <a href="${dlUrl}" target="_blank" class="attachment-badge" title="Download PDF">
                                📄 ${escapeHTML(att.caption || att.original_filename)} (${Math.round((att.file_size_bytes || 0) / 1024)} KB)
                            </a>
                        `;
                    }
                }).join("") + `</div>`;
            }

            const canPublish = currentAuthUser && (currentAuthUser.role === "admin" || currentAuthUser.role === "staff");
            const toggleBtnHtml = canPublish
                ? `<button type="button" class="btn-publish-toggle ${isPub ? 'btn-unpublish' : 'btn-publish'}" onclick="togglePublishUpdate(${up.id}, ${!isPub}, ${woId})">
                        ${isPub ? 'Unpublish' : 'Publish to Public'}
                   </button>`
                : "";

            item.innerHTML = `
                <div class="wo-update-meta-row">
                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                        <strong>Update #${up.id}</strong>
                        <span>by <em>${escapeHTML(up.author_username || 'Officer')}</em></span>
                        <span>&bull;</span>
                        <span>${escapeHTML(up.created_at)}</span>
                        ${statusBadge}
                        ${pubBadge}
                    </div>
                    <div>
                        ${toggleBtnHtml}
                    </div>
                </div>

                ${correctionHtml}

                <div style="display: flex; align-items: center; gap: 8px; margin-top: 2px;">
                    <span style="font-size: 11.5px; font-weight: 600; color: #0284c7;">Progress: ${up.progress_pct}%</span>
                    <div class="progress-bar-container" style="flex: 1; height: 6px; margin: 0;">
                        <div class="progress-bar-fill" style="width: ${up.progress_pct}%;"></div>
                    </div>
                </div>

                <div style="font-size: 12.5px; color: #0f172a; margin-top: 4px; line-height: 1.4;">
                    <strong>Work:</strong> ${escapeHTML(up.work_completed)}
                </div>

                ${up.blockers ? `<div style="font-size: 11.5px; color: #dc2626;"><strong>Blockers:</strong> ${escapeHTML(up.blockers)}</div>` : ""}
                ${up.next_actions ? `<div style="font-size: 11.5px; color: #0369a1;"><strong>Next Actions:</strong> ${escapeHTML(up.next_actions)}</div>` : ""}
                ${up.estimated_completion_date ? `<div style="font-size: 11px; color: #64748b;"><strong>Est. Completion:</strong> ${escapeHTML(up.estimated_completion_date)}</div>` : ""}

                ${internalNotesHtml}
                ${attachmentsHtml}
            `;

            modalWoUpdatesList.appendChild(item);
        });

    } catch (err) {
        modalWoUpdatesList.innerHTML = `<span style="color: #dc2626; font-size: 12px;">Error: ${escapeHTML(err.message)}</span>`;
    }
};

/**
 * Toggle publication state of an update.
 */
window.togglePublishUpdate = async function(updateId, makePublished, woId) {
    try {
        const endpoint = makePublished
            ? `/api/work-orders/updates/${updateId}/publish`
            : `/api/work-orders/updates/${updateId}/unpublish`;

        const res = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ publish: makePublished }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Could not toggle publication.");

        // Refresh updates timeline and public feed
        await loadWorkOrderUpdates(woId);
        await loadPublicProgress();

    } catch (err) {
        alert("Publish error: " + err.message);
    }
};

/**
 * Submit progress update form.
 */
if (formPostProgressUpdate) {
    formPostProgressUpdate.addEventListener("submit", async (e) => {
        e.preventDefault();

        const woId = document.getElementById("edit-wo-id").value;
        if (!woId) return;

        const workDone = updateWorkCompleted ? updateWorkCompleted.value.trim() : "";
        const progressVal = updateProgressPct ? parseInt(updateProgressPct.value || "0", 10) : 0;
        const newStatus = updateStatusSelect ? updateStatusSelect.value : "";
        const estComp = updateEstCompletion ? updateEstCompletion.value : "";
        const blockers = updateBlockers ? updateBlockers.value.trim() : "";
        const nextActions = updateNextActions ? updateNextActions.value.trim() : "";
        const internalNotes = updateInternalNotes ? updateInternalNotes.value.trim() : "";
        const isPublished = updateIsPublishedCheckbox ? updateIsPublishedCheckbox.checked : false;
        const isCorrection = updateIsCorrectionCheckbox ? updateIsCorrectionCheckbox.checked : false;
        const correctionForId = updateCorrectionForIdSelect ? updateCorrectionForIdSelect.value : "";
        const caption = updateCaptionInput ? updateCaptionInput.value.trim() : "";

        if (!workDone) {
            alert("Please provide a summary of work completed.");
            return;
        }

        const formData = new FormData();
        formData.append("work_completed", workDone);
        formData.append("progress_pct", progressVal);
        if (newStatus) formData.append("status", newStatus);
        if (estComp) formData.append("estimated_completion_date", estComp);
        if (blockers) formData.append("blockers", blockers);
        if (nextActions) formData.append("next_actions", nextActions);
        if (internalNotes) formData.append("internal_notes", internalNotes);
        formData.append("is_published", isPublished ? "true" : "false");

        if (isCorrection && correctionForId) {
            formData.append("is_correction", "true");
            formData.append("correction_for_id", correctionForId);
        }

        if (updateAttachmentInput && updateAttachmentInput.files.length > 0) {
            formData.append("attachments", updateAttachmentInput.files[0]);
            if (caption) formData.append("caption", caption);
        }

        if (btnSubmitProgressUpdate) btnSubmitProgressUpdate.disabled = true;
        if (modalUpdateFormMsg) {
            modalUpdateFormMsg.textContent = "Posting update...";
            modalUpdateFormMsg.style.color = "#0284c7";
        }

        try {
            const res = await fetch(`/api/work-orders/${woId}/updates`, {
                method: "POST",
                body: formData,
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "Failed to post progress update.");
            }

            if (modalUpdateFormMsg) {
                modalUpdateFormMsg.textContent = "✓ Progress update posted successfully!";
                modalUpdateFormMsg.style.color = "#059669";
            }

            // Reset form fields
            if (updateWorkCompleted) updateWorkCompleted.value = "";
            if (updateBlockers) updateBlockers.value = "";
            if (updateNextActions) updateNextActions.value = "";
            if (updateInternalNotes) updateInternalNotes.value = "";
            if (updateAttachmentInput) updateAttachmentInput.value = "";
            if (updateCaptionInput) updateCaptionInput.value = "";

            // Reload updates, work-orders queue, and public feed
            await loadWorkOrderUpdates(woId);
            await loadWorkOrdersQueue();
            await loadPublicProgress();

            setTimeout(() => {
                if (modalUpdateFormMsg) modalUpdateFormMsg.textContent = "";
            }, 3000);

        } catch (err) {
            if (modalUpdateFormMsg) {
                modalUpdateFormMsg.textContent = `Error: ${err.message}`;
                modalUpdateFormMsg.style.color = "#dc2626";
            }
        } finally {
            if (btnSubmitProgressUpdate) btnSubmitProgressUpdate.disabled = false;
        }
    });
}

/**
 * Pan Leaflet map to report coordinates and open marker popup.
 */
window.panToReportCoords = function(lat, lng, label) {
    if (!lat || !lng || !map) return;
    map.setView([lat, lng], 16, { animate: true });

    // Scroll to map smoothly
    const mapEl = document.getElementById("map");
    if (mapEl) mapEl.scrollIntoView({ behavior: "smooth", block: "center" });

    // Open popup
    L.popup()
        .setLatLng([lat, lng])
        .setContent(`<strong>${escapeHTML(label || 'Report Location')}</strong><br>Lat: ${lat.toFixed(5)}, Lng: ${lng.toFixed(5)}`)
        .openOn(map);
};

/**
 * Load and render public progress updates feed.
 */
window.loadPublicProgress = async function() {
    if (!publicProgressList) return;

    const statusVal = filterPublicStatus ? filterPublicStatus.value : "";
    const searchVal = filterPublicSearch ? filterPublicSearch.value.trim() : "";
    const offset = (publicProgressPage - 1) * publicProgressLimit;

    const params = new URLSearchParams({
        limit: publicProgressLimit.toString(),
        offset: offset.toString(),
    });
    if (statusVal) params.set("status", statusVal);
    if (searchVal) params.set("search", searchVal);

    try {
        const res = await fetch(`/api/public/progress?${params.toString()}`);
        if (!res.ok) throw new Error("Could not load public progress updates.");

        const data = await res.json();
        totalPublicUpdates = data.total || 0;
        const updates = data.updates || [];

        if (publicProgressCountBadge) {
            publicProgressCountBadge.textContent = `${totalPublicUpdates} published update${totalPublicUpdates === 1 ? '' : 's'}`;
        }

        if (updates.length === 0) {
            publicProgressList.innerHTML = `
                <p id="no-public-progress-msg" style="color: #64748b; font-style: italic; padding: 12px 0;">
                    No published repair updates match your criteria.
                </p>
            `;
            updatePublicPaginationControls();
            return;
        }

        publicProgressList.innerHTML = "";

        updates.forEach(up => {
            const card = document.createElement("div");
            card.className = "public-progress-card";

            const statusBadgeClass = getStatusBadgeClass(up.status);
            const progress = up.progress_pct != null ? up.progress_pct : 0;

            let attachmentsHtml = "";
            if (up.attachments && up.attachments.length > 0) {
                attachmentsHtml = `<div class="attachment-container">` + up.attachments.map(att => {
                    const dlUrl = att.download_url;
                    if (att.file_type === "image") {
                        return `
                            <a href="${dlUrl}" target="_blank" title="${escapeHTML(att.caption || 'Progress Photo')}" style="display: inline-block;">
                                <img src="${dlUrl}" class="attachment-thumbnail" alt="${escapeHTML(att.caption || 'Photo')}">
                            </a>
                        `;
                    } else {
                        return `
                            <a href="${dlUrl}" target="_blank" class="attachment-badge" title="View PDF Document">
                                📄 ${escapeHTML(att.caption || att.original_filename)} (${Math.round((att.file_size_bytes || 0) / 1024)} KB)
                            </a>
                        `;
                    }
                }).join("") + `</div>`;
            }

            const mapBtn = (up.latitude && up.longitude)
                ? `<button type="button" class="btn-secondary" style="font-size: 11.5px; padding: 4px 10px;" onclick="panToReportCoords(${up.latitude}, ${up.longitude}, '${escapeHTML(up.road_name)}')">📍 View on Map</button>`
                : "";

            card.innerHTML = `
                <div class="public-progress-header">
                    <div>
                        <div class="public-progress-title">🛣️ ${escapeHTML(up.road_name || 'Municipal Road')}</div>
                        <div style="font-size: 11.5px; color: #64748b; margin-top: 2px;">
                            Work Order: <span class="public-progress-code">${escapeHTML(up.work_order_code || 'WO')}</span>
                            &bull; Updated: ${escapeHTML(up.created_at || '')}
                        </div>
                    </div>
                    <span class="badge ${statusBadgeClass}" style="font-size: 12px;">${escapeHTML(up.status || 'Active')}</span>
                </div>

                <div>
                    <div class="progress-bar-label">
                        <span>Repair Progress</span>
                        <span><strong>${progress}%</strong></span>
                    </div>
                    <div class="progress-bar-container">
                        <div class="progress-bar-fill" style="width: ${progress}%;"></div>
                    </div>
                </div>

                <div style="font-size: 13px; color: #1e293b; line-height: 1.45;">
                    <strong>Milestone Summary:</strong> ${escapeHTML(up.work_completed)}
                </div>

                ${up.blockers ? `<div style="font-size: 12px; color: #dc2626;"><strong>Current Blockers:</strong> ${escapeHTML(up.blockers)}</div>` : ""}
                ${up.next_actions ? `<div style="font-size: 12px; color: #0284c7;"><strong>Next Steps:</strong> ${escapeHTML(up.next_actions)}</div>` : ""}
                ${up.estimated_completion_date ? `<div style="font-size: 11.5px; color: #64748b;"><strong>Target Completion:</strong> ${escapeHTML(up.estimated_completion_date)}</div>` : ""}

                ${attachmentsHtml}

                <div style="display: flex; justify-content: flex-end; margin-top: 4px;">
                    ${mapBtn}
                </div>
            `;

            publicProgressList.appendChild(card);
        });

        updatePublicPaginationControls();

    } catch (err) {
        publicProgressList.innerHTML = `<p style="color: #dc2626; font-size: 13px;">Error loading progress feed: ${escapeHTML(err.message)}</p>`;
    }
};

function updatePublicPaginationControls() {
    const totalPages = Math.max(1, Math.ceil(totalPublicUpdates / publicProgressLimit));
    if (publicPageIndicator) {
        publicPageIndicator.textContent = `Page ${publicProgressPage} of ${totalPages}`;
    }
    if (btnPublicPrev) btnPublicPrev.disabled = (publicProgressPage <= 1);
    if (btnPublicNext) btnPublicNext.disabled = (publicProgressPage >= totalPages);
}

if (applyPublicFiltersBtn) {
    applyPublicFiltersBtn.addEventListener("click", () => {
        publicProgressPage = 1;
        loadPublicProgress();
    });
}

if (resetPublicFiltersBtn) {
    resetPublicFiltersBtn.addEventListener("click", () => {
        if (filterPublicStatus) filterPublicStatus.value = "";
        if (filterPublicSearch) filterPublicSearch.value = "";
        publicProgressPage = 1;
        loadPublicProgress();
    });
}

if (refreshPublicProgressBtn) {
    refreshPublicProgressBtn.addEventListener("click", () => loadPublicProgress());
}

if (btnPublicPrev) {
    btnPublicPrev.addEventListener("click", () => {
        if (publicProgressPage > 1) {
            publicProgressPage--;
            loadPublicProgress();
        }
    });
}

if (btnPublicNext) {
    btnPublicNext.addEventListener("click", () => {
        const totalPages = Math.ceil(totalPublicUpdates / publicProgressLimit);
        if (publicProgressPage < totalPages) {
            publicProgressPage++;
            loadPublicProgress();
        }
    });
}

// Initial bootstrap of Stage 8 Public Road-Repair Progress Updates
(function initStage8() {
    loadPublicProgress();
})();

// ============================================================
// Officer Direct Layer in Road Repair Progress
// ============================================================

function updateOfficerLayerUI(isLoggedIn, user) {
    const layerLoggedOut = document.getElementById("officer-layer-logged-out");
    const layerLoggedIn = document.getElementById("officer-layer-logged-in");
    const officerUsername = document.getElementById("officer-layer-username");

    if (isLoggedIn && user && (user.role === "staff" || user.role === "admin")) {
        if (layerLoggedOut) layerLoggedOut.style.display = "none";
        if (layerLoggedIn) layerLoggedIn.style.display = "block";
        if (officerUsername) officerUsername.textContent = `${user.username} (${user.role})`;
        populateOfficerWorkOrderDropdown();
    } else {
        if (layerLoggedOut) layerLoggedOut.style.display = "flex";
        if (layerLoggedIn) layerLoggedIn.style.display = "none";
    }
}

async function populateOfficerWorkOrderDropdown() {
    const sel = document.getElementById("officer-direct-wo-select");
    if (!sel) return;

    try {
        const res = await fetch("/api/work-orders");
        if (!res.ok) return;
        const data = await res.json();
        const workOrders = data.work_orders || [];

        sel.innerHTML = `<option value="">-- Choose active work order (${workOrders.length} available) --</option>`;

        workOrders.forEach(wo => {
            const rep = wo.linked_report || {};
            const opt = document.createElement("option");
            opt.value = wo.id;
            opt.textContent = `${wo.work_order_code}: ${rep.road_name || 'Road'} (${wo.status} - ${rep.damage_type || 'Defect'})`;
            opt.dataset.targetDate = wo.target_date || "";
            opt.dataset.status = wo.status || "Open";
            sel.appendChild(opt);
        });

    } catch (err) {
        console.warn("Could not populate officer work order dropdown:", err);
    }
}

const btnOfficerLayerLogin = document.getElementById("btn-officer-layer-login");
if (btnOfficerLayerLogin) {
    btnOfficerLayerLogin.addEventListener("click", () => {
        openLoginModal("Officer login is required to post repair progress and completed photos.");
    });
}

const btnToggleOfficerForm = document.getElementById("btn-toggle-officer-form");
const formOfficerDirect = document.getElementById("form-officer-direct-update");
if (btnToggleOfficerForm && formOfficerDirect) {
    btnToggleOfficerForm.addEventListener("click", () => {
        const isHidden = formOfficerDirect.style.display === "none" || !formOfficerDirect.style.display;
        formOfficerDirect.style.display = isHidden ? "block" : "none";
        btnToggleOfficerForm.textContent = isHidden ? "✕ Close Form" : "+ Post Progress / Completion";
        if (isHidden) {
            populateOfficerWorkOrderDropdown();
        }
    });
}

const btnCancelOfficerDirect = document.getElementById("btn-cancel-officer-direct");
if (btnCancelOfficerDirect && formOfficerDirect) {
    btnCancelOfficerDirect.addEventListener("click", () => {
        formOfficerDirect.style.display = "none";
        if (btnToggleOfficerForm) btnToggleOfficerForm.textContent = "+ Post Progress / Completion";
    });
}

const officerDirectStatus = document.getElementById("officer-direct-status");
const officerDirectProgressPct = document.getElementById("officer-direct-progress-pct");
const officerDirectCaption = document.getElementById("officer-direct-caption");

if (officerDirectStatus) {
    officerDirectStatus.addEventListener("change", () => {
        if (officerDirectStatus.value === "Completed") {
            if (officerDirectProgressPct) officerDirectProgressPct.value = "100";
            if (officerDirectCaption) officerDirectCaption.value = "Maintenance completed photograph";
        } else if (officerDirectStatus.value === "In Progress") {
            if (officerDirectProgressPct) officerDirectProgressPct.value = "50";
            if (officerDirectCaption) officerDirectCaption.value = "In-progress maintenance photograph";
        }
    });
}

const officerDirectWoSelect = document.getElementById("officer-direct-wo-select");
const officerDirectEstDate = document.getElementById("officer-direct-est-date");
if (officerDirectWoSelect && officerDirectEstDate) {
    officerDirectWoSelect.addEventListener("change", () => {
        const selected = officerDirectWoSelect.options[officerDirectWoSelect.selectedIndex];
        if (selected && selected.dataset.targetDate) {
            officerDirectEstDate.value = selected.dataset.targetDate;
        }
    });
}

if (formOfficerDirect) {
    formOfficerDirect.addEventListener("submit", async (e) => {
        e.preventDefault();
        const woId = officerDirectWoSelect ? officerDirectWoSelect.value : "";
        if (!woId) {
            alert("Please select a work order to update.");
            return;
        }

        const workDone = document.getElementById("officer-direct-work-completed").value.trim();
        if (!workDone) {
            alert("Please enter a summary of the maintenance work completed.");
            return;
        }

        const statusVal = officerDirectStatus ? officerDirectStatus.value : "Completed";
        const progressVal = officerDirectProgressPct ? officerDirectProgressPct.value : "100";
        const estDate = officerDirectEstDate ? officerDirectEstDate.value : "";
        const blockers = document.getElementById("officer-direct-blockers").value.trim();
        const internalNotes = document.getElementById("officer-direct-internal-notes").value.trim();
        const isPublished = document.getElementById("officer-direct-is-published").checked;
        const photoInput = document.getElementById("officer-direct-photo");
        const captionVal = officerDirectCaption ? officerDirectCaption.value.trim() : "Maintenance completed photograph";

        const formData = new FormData();
        formData.append("work_completed", workDone);
        formData.append("progress_pct", progressVal);
        formData.append("status", statusVal);
        if (estDate) formData.append("estimated_completion_date", estDate);
        if (blockers) formData.append("blockers", blockers);
        if (internalNotes) formData.append("internal_notes", internalNotes);
        formData.append("is_published", isPublished ? "true" : "false");

        if (photoInput && photoInput.files.length > 0) {
            formData.append("attachments", photoInput.files[0]);
            formData.append("caption", captionVal);
        }

        const submitBtn = document.getElementById("btn-submit-officer-direct");
        const msgEl = document.getElementById("officer-direct-msg");

        if (submitBtn) submitBtn.disabled = true;
        if (msgEl) {
            msgEl.textContent = "Posting and publishing completed repair details...";
            msgEl.style.color = "#0284c7";
        }

        try {
            const res = await fetch(`/api/work-orders/${woId}/updates`, {
                method: "POST",
                body: formData,
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "Failed to post completed repair.");
            }

            if (msgEl) {
                msgEl.textContent = "✓ Maintenance completed update & photograph posted and published successfully!";
                msgEl.style.color = "#059669";
            }

            // Reset form
            formOfficerDirect.reset();
            if (officerDirectStatus) officerDirectStatus.value = "Completed";
            if (officerDirectProgressPct) officerDirectProgressPct.value = "100";
            if (officerDirectCaption) officerDirectCaption.value = "Maintenance completed photograph";

            // Reload public feed & work orders queue & repaired map layer
            await loadPublicProgress();
            await loadWorkOrdersQueue();
            if (activeMapMode === "repaired") {
                fetchAndRenderRepairedRoads();
            }

            setTimeout(() => {
                if (msgEl) msgEl.textContent = "";
                formOfficerDirect.style.display = "none";
                if (btnToggleOfficerForm) btnToggleOfficerForm.textContent = "+ Post Progress / Completion";
            }, 3000);

        } catch (err) {
            if (msgEl) {
                msgEl.textContent = `Error: ${err.message}`;
                msgEl.style.color = "#dc2626";
            }
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });
}
