===================
Developer Changelog
===================

This changelog documents API changes, schema modifications, and other developer-relevant changes that may affect integrations with rotki.

Unreleased
==========

Matching Asset Movements With Onchain Events
--------------------------------------------

Exchange asset movement events may now be manually matched with specific onchain events via the API.

* **New Endpoint**: ``PUT /api/(version)/history/events/match/asset_movements``

  - Match asset movements with corresponding events or mark asset movements as having no match.
  - Required ``asset_movement`` parameter specifying the DB identifier of the asset movement.
  - Optional ``matched_event`` parameter specifying the DB identifier of the event to match with the asset movement. If this parameter is omitted or set to null, the asset movement is marked as having no match.
  - Example: ``{"asset_movement": 123, "matched_event": 124}``

* **New Endpoint**: ``POST /api/(version)/history/events/match/asset_movements``

  - Finds possible matches for a given asset movement within the specified time range.
  - Required ``asset_movement`` parameter specifying the group identifier to find matches for.
  - Optional ``time_range`` parameter specifying the time range in seconds to include. Defaults to 7200 (2 hours).
  - Example: ``{"asset_movement": "ef2...69f", "time_range": 7200}``

* **New Endpoint**: ``GET /api/(version)/history/events/match/asset_movements``

  - Takes no parameters.
  - Returns a list of group identifiers of any unmatched asset movements in the DB that are not marked as having no match.

* **Modified Endpoint**: ``POST /api/(version)/history/events``

  - New optional ``actual_group_identifier`` field in the response, containing the actual group identifier of the event as stored in the DB.
  - This change preserves the actual group identifier when asset movements are combined with the group of their matched event for display as a single unit in the frontend.

Event/Group Identifier Renaming
-------------------------------

The common identifier for groups of events (i.e. all events from a given EVM tx) is renamed from ``event_identifier`` to ``group_identifier``.

* **Modified Endpoints**:

  - ``POST``, ``PUT``, and ``PATCH`` on ``/api/(version)/history/events`` - Renamed ``event_identifier`` to ``group_identifier``.
  - ``PUT /api/(version)/history/events/export`` - Renamed ``event_identifiers`` to ``group_identifiers``.
  - ``POST /api/(version)/history/debug`` - Renamed ``event_identifier`` to ``group_identifier``.
  - ``POST /api/(version)/balances/historical/asset`` - Renamed ``last_event_identifier`` to ``last_group_identifier``.
  - ``POST /api/(version)/balances/historical/netvalue`` - Renamed ``last_event_identifier`` to ``last_group_identifier``.


:releasetag:`1.41.1`
====================

Runtime Log Level Modification
------------------------------

The backend log level may now be modified at runtime without restarting.

* **Modified Endpoint**: ``GET /api/(version)/settings/configuration``

  - Now includes a ``loglevel`` field in response
  - Example: ``{"loglevel": {"value": "DEBUG", "is_default": true}, ...}``

* **New Endpoint**: ``PUT /api/(version)/settings/configuration``

  - Currently only supports the ``loglevel`` parameter
  - Accepted values: ``TRACE``, ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``
  - Returns the same format as the GET endpoint.

ERC-721 (NFT) Token IDs
----------------------------------------

ERC-721 token IDs may now be set when adding/editing assets.

* **Modified Endpoint**: ``PUT /api/(version)/assets/all``

  - Supports a new ``collectible_id`` field. Only valid when token_kind is ``"erc721"``.

* **Modified Endpoint**: ``GET /api/(version)/assets/all``

  - Now includes a ``collectible_id`` field in the response when token_kind is ``"erc721"``.



