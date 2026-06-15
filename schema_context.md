# Storm Events database — schema and rules

Table `storm_events` has 2,023,627 rows covering 1950–2026.

## Columns

- `event_id` (BIGINT) — Unique id for a single storm event.
- `episode_id` (BIGINT) — Id grouping related events from the same weather episode.
- `state` (VARCHAR) — US state/territory name in UPPERCASE, e.g. 'TEXAS', 'FLORIDA'.
- `cz_name` (VARCHAR) — County or forecast-zone name where the event occurred.
- `cz_type` (VARCHAR) — 'C' = county/parish, 'Z' = NWS forecast zone, 'M' = marine.
- `wfo` (VARCHAR) — NWS Weather Forecast Office that issued the report.
- `year` (BIGINT) — Year of the event (integer).
- `month_name` (VARCHAR) — Full month name, e.g. 'April'.
- `cz_timezone` (VARCHAR) — Timezone of the begin/end times, e.g. 'CST-6'.
- `event_type` (VARCHAR) — The kind of weather event. USE EXACT STRINGS (see value list).
- `injuries_direct` (BIGINT) — Injuries directly caused by the event.
- `injuries_indirect` (BIGINT) — Injuries indirectly caused by the event.
- `deaths_direct` (BIGINT) — Deaths directly caused by the event.
- `deaths_indirect` (BIGINT) — Deaths indirectly caused by the event.
- `magnitude` (BIGINT) — Numeric magnitude: wind speed (knots) for wind, hail size (inches) for hail.
- `magnitude_type` (VARCHAR) — How magnitude was measured, e.g. 'EG', 'MG', 'MS', 'ES'.
- `tor_f_scale` (VARCHAR) — Tornado intensity, e.g. 'EF0'..'EF5' (or legacy 'F0'..'F5').
- `tor_length` (DOUBLE) — Tornado path length in miles.
- `tor_width` (BIGINT) — Tornado path width in yards.
- `flood_cause` (VARCHAR) — Cause of a flood event, when applicable.
- `category` (VARCHAR) — Event category, when applicable.
- `source` (VARCHAR) — Who reported the event, e.g. 'Trained Spotter', 'Public'.
- `begin_lat` (DOUBLE) — Latitude where the event began.
- `begin_lon` (DOUBLE) — Longitude where the event began.
- `end_lat` (DOUBLE) — Latitude where the event ended.
- `end_lon` (DOUBLE) — Longitude where the event ended.
- `begin_location` (VARCHAR) — Nearest place name to where the event began.
- `end_location` (VARCHAR) — Nearest place name to where the event ended.
- `data_source` (VARCHAR) — NOAA data source code.
- `damage_property` (DOUBLE) — Property damage in US dollars (numeric; NULL if not reported).
- `damage_crops` (DOUBLE) — Crop damage in US dollars (numeric; NULL if not reported).
- `begin_datetime` (TIMESTAMP) — Timestamp when the event began.
- `end_datetime` (TIMESTAMP) — Timestamp when the event ended.

## CRITICAL: data completeness by year

Event recording expanded over time, so the same event types are NOT available for all years:
- 1950–1954: only Tornado events.
- 1955–1995: only Tornado, Thunderstorm Wind, and Hail.
- 1996–present: all ~50 event types.
When a question implies a trend across these boundaries (e.g. floods since 1950), warn that pre-1996 absence means *missing data*, not zero occurrences.

## Exact event_type values (use these strings verbatim)

- 'Thunderstorm Wind'  (564,601)
- 'Hail'  (419,169)
- 'Flash Flood'  (110,337)
- 'High Wind'  (96,811)
- 'Winter Storm'  (91,955)
- 'Winter Weather'  (84,694)
- 'Drought'  (82,518)
- 'Tornado'  (80,402)
- 'Heavy Snow'  (75,954)
- 'Flood'  (70,189)
- 'Marine Thunderstorm Wind'  (43,140)
- 'Heat'  (34,898)
- 'Heavy Rain'  (31,516)
- 'Strong Wind'  (28,243)
- 'Excessive Heat'  (21,179)
- 'Extreme Cold/Wind Chill'  (19,289)
- 'Cold/Wind Chill'  (19,130)
- 'Lightning'  (18,171)
- 'Dense Fog'  (17,776)
- 'Blizzard'  (17,153)
- 'Frost/Freeze'  (15,244)
- 'Ice Storm'  (12,632)
- 'High Surf'  (10,640)
- 'Funnel Cloud'  (9,941)
- 'Wildfire'  (9,385)
- 'Tropical Storm'  (7,146)
- 'Waterspout'  (6,238)
- 'Coastal Flood'  (4,647)
- 'Lake-Effect Snow'  (2,880)
- 'Debris Flow'  (2,522)
- 'Hurricane (Typhoon)'  (2,149)
- 'Dust Storm'  (2,066)
- 'Rip Current'  (1,891)
- 'Storm Surge/Tide'  (1,656)
- 'Marine High Wind'  (997)
- 'Avalanche'  (869)
- 'Sleet'  (859)
- 'Marine Hail'  (856)
- 'Astronomical Low Tide'  (784)
- 'Marine Tropical Storm'  (603)
- 'Tropical Depression'  (552)
- 'Freezing Fog'  (502)
- 'Lakeshore Flood'  (359)
- 'Dust Devil'  (255)
- 'Marine Strong Wind'  (166)
- 'Dense Smoke'  (147)
- 'Marine Hurricane/Typhoon'  (109)
- 'Volcanic Ashfall'  (78)
- 'Seiche'  (76)
- 'Volcanic Ash'  (70)
- 'Sneakerwave'  (68)
- 'Tsunami'  (52)
- 'Marine Tropical Depression'  (31)
- 'Marine Dense Fog'  (22)
- 'Northern Lights'  (8)
- 'Marine Lightning'  (2)

## Notes for writing correct SQL

- `state` values are UPPERCASE full names, e.g. 'TEXAS'.
- Damage columns are already numeric dollars; do NOT parse text.
- Deaths/injuries come in direct and indirect columns; add both for totals unless asked otherwise.
- Use `begin_datetime` for date/time filtering.
- 69 distinct state/territory values are present.

## Example questions and correct SQL

Q: How many tornadoes occurred in Texas in 2011?
```sql
SELECT COUNT(*) FROM storm_events
WHERE event_type = 'Tornado' AND state = 'TEXAS' AND year = 2011;
```

Q: What was the total property damage from hurricanes in 2005?
```sql
SELECT SUM(damage_property) FROM storm_events
WHERE event_type = 'Hurricane (Typhoon)' AND year = 2005;
```

Q: Which 5 states had the most hail events since 2015?
```sql
SELECT state, COUNT(*) AS hail_events FROM storm_events
WHERE event_type = 'Hail' AND year >= 2015
GROUP BY state ORDER BY hail_events DESC LIMIT 5;
```

Q: What were the 10 deadliest events in 2011 by total deaths?
```sql
SELECT event_type, state, cz_name, begin_datetime,
       (deaths_direct + deaths_indirect) AS total_deaths
FROM storm_events WHERE year = 2011
ORDER BY total_deaths DESC LIMIT 10;
```

Q: How many flood events were there each year since 2000?
```sql
SELECT year, COUNT(*) AS floods FROM storm_events
WHERE event_type IN ('Flood', 'Flash Flood') AND year >= 2000
GROUP BY year ORDER BY year;
```
