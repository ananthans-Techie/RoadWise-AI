-- RoadWise AI Database Dump (Pure-Python Fallback)
-- Generated at: 2026-10-28T08:51:01.770500+00:00 UTC

BEGIN;

-- Data for table: road_damage_reports
INSERT INTO road_damage_reports (id, road_name, damage_type, severity, description, photo_url, geom, created_at) VALUES (1, 'Mangalapuram, Trivandrum', 'Alligator crack', 'Moderate', 'More problem while riding', '/static/uploads/report_20261021_162532_8ec5d565.jpg', ST_GeomFromText('POINT(76.846417 8.626098)', 4326), '2026-10-21T21:55:32.866392+05:30') ON CONFLICT (id) DO NOTHING;
INSERT INTO road_damage_reports (id, road_name, damage_type, severity, description, photo_url, geom, created_at) VALUES (2, 'NH Thonnakkal, Trivandrum Keralam', 'Pothole', 'Moderate', 'It is in the NH, so the travelling is more dangerous', '/static/uploads/report_20261023_130112_9fee589c.jpg', ST_GeomFromText('POINT(76.846925 8.63114)', 4326), '2026-10-23T18:31:13.343151+05:30') ON CONFLICT (id) DO NOTHING;
INSERT INTO road_damage_reports (id, road_name, damage_type, severity, description, photo_url, geom, created_at) VALUES (3, 'Sreekaryam Junction, Trivandrum, Keralam', 'Pothole', 'Critical', 'This is a very relevant problem immediately, Maintenance needed', '/static/uploads/report_20261023_162434_9d8dc81e.jpg', ST_GeomFromText('POINT(76.917068 8.548579)', 4326), '2026-10-23T21:54:34.352729+05:30') ON CONFLICT (id) DO NOTHING;
INSERT INTO road_damage_reports (id, road_name, damage_type, severity, description, photo_url, geom, created_at) VALUES (4, 'Kaiyapuram- Kazhakoottam, Trivandrum, Keralam', 'Surface deterioration', 'Moderate', 'This is in NH, so immediate maintenance is needed', '/static/uploads/report_20261028_064912_8520b2c9.jpg', ST_GeomFromText('POINT(76.867647 8.576258)', 4326), '2026-10-28T12:19:12.656196+05:30') ON CONFLICT (id) DO NOTHING;

-- Data for table: road_segments

-- Data for table: junctions

-- Data for table: traffic_counts

-- Data for table: report_priority_scores

-- Data for table: work_orders

-- Data for table: work_order_status_history

COMMIT;
