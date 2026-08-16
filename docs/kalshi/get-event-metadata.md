Source: https://docs.kalshi.com/api-reference/events/get-event-metadata.md

# Get Event Metadata

Separate endpoint from plain Get Event - lightweight, visual/structural
metadata only.

## Route

`GET /events/{event_ticker}/metadata`

## Response shape

```
GetEventMetadataResponse {
  image_url* (string)              - event image path
  featured_image_url (string, optional)
  market_details* (array)          - [{market_ticker, image_url, color_code}]
  settlement_sources* (array)      - [{name, url}]
  competition (string, nullable)
  competition_scope (string, nullable)
}
```
(* = required fields)

## Key finding (2026-08-15 audit)

**No schedule/start-time data of any kind** - this endpoint is purely
images, market-card visuals, and settlement sources. It does NOT answer
the "what time does this real-world event start" question
`services/event_schedule.py` was built to solve - that module's own
4-source waterfall (Event.strike_date, milestone start_date, rules-text
regex, web search) remains the right approach; this endpoint isn't a
5th source worth adding.

This file is a local copy of the fetched page content used during this
session.
