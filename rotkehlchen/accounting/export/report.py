from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rotkehlchen.accounting.export.csv import CSVExporter
from rotkehlchen.accounting.mixins.event import AccountingEventType
from rotkehlchen.accounting.pnl import PNL, PnlTotals
from rotkehlchen.assets.asset import Asset
from rotkehlchen.db.filtering import ReportDataFilterQuery
from rotkehlchen.db.reports import DBAccountingReports
from rotkehlchen.errors.misc import InputError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.premium.premium import UserLimitType, get_user_limit
from rotkehlchen.types import CostBasisMethod, Timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from rotkehlchen.accounting.structures.processed_event import ProcessedAccountingEvent
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.premium.premium import Premium

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


def get_report_events_and_overview(
        database: DBHandler,
        premium: Premium | None,
        report_id: int,
) -> tuple[list[ProcessedAccountingEvent] | None, dict[str, Any] | None, str | None]:
    dbreports = DBAccountingReports(database)
    reports, _ = dbreports.get_reports(report_id=report_id, limit=1)
    if len(reports) == 0:
        return None, None, f'PnL report with id {report_id} was not found'

    try:
        events, _, _ = dbreports.get_report_data(
            filter_=ReportDataFilterQuery.make(report_id=report_id),
            limit=get_user_limit(
                premium=premium,
                limit_type=UserLimitType.PNL_EVENTS,
            )[0],
        )
    except InputError as e:
        return None, None, str(e)

    if len(events) == 0:
        return None, None, 'No report events found in order to perform an export'

    return events, reports[0], None


def apply_report_settings_to_csv_exporter(
        csv_exporter: CSVExporter,
        report_settings: dict[str, Any],
) -> None:
    settings = csv_exporter.settings
    if (profit_currency := report_settings.get('profit_currency')) is not None:
        settings.main_currency = Asset(str(profit_currency)).resolve_to_asset_with_oracles()
    if 'taxfree_after_period' in report_settings:
        settings.taxfree_after_period = report_settings['taxfree_after_period']
    if 'include_crypto2crypto' in report_settings:
        settings.include_crypto2crypto = report_settings['include_crypto2crypto']
    if 'calculate_past_cost_basis' in report_settings:
        settings.calculate_past_cost_basis = report_settings['calculate_past_cost_basis']
    if 'include_gas_costs' in report_settings:
        settings.include_gas_costs = report_settings['include_gas_costs']
    if (cost_basis_method := report_settings.get('cost_basis_method')) is not None:
        settings.cost_basis_method = CostBasisMethod.deserialize(str(cost_basis_method))
    if 'eth_staking_taxable_after_withdrawal_enabled' in report_settings:
        settings.eth_staking_taxable_after_withdrawal_enabled = report_settings[
            'eth_staking_taxable_after_withdrawal_enabled'
        ]
    if 'include_fees_in_cost_basis' in report_settings:
        settings.include_fees_in_cost_basis = report_settings['include_fees_in_cost_basis']


def export_pnl_report_csv_from_db(
        database: DBHandler,
        premium: Premium | None,
        report_id: int,
        directory_path: Path | None,
) -> tuple[bool | dict[str, Any] | None, str]:
    """Export a report's CSV from transient DB data and return (result, message)."""
    events, report, error = get_report_events_and_overview(
        database=database,
        premium=premium,
        report_id=report_id,
    )
    if error is not None:
        return None, error
    if events is None or report is None:
        return None, 'No report data found in order to perform an export'

    pnls = PnlTotals()
    for event_type, entry in report['overview'].items():
        try:
            pnl_type = AccountingEventType.deserialize(event_type)
        except DeserializationError as e:
            log.error(f'Failed to deserialize PnL report overview type {event_type}: {e!s}')
            continue
        pnls[pnl_type] = PNL(taxable=FVal(entry['taxable']), free=FVal(entry['free']))

    csv_exporter = CSVExporter(database)
    csv_exporter.reset(
        start_ts=Timestamp(report['start_ts']),
        end_ts=Timestamp(report['end_ts']),
    )
    apply_report_settings_to_csv_exporter(
        csv_exporter=csv_exporter,
        report_settings=report['settings'],
    )

    if directory_path is None:
        success, zipfile = csv_exporter.create_zip(events=events, pnls=pnls)
        if success is False:
            return None, 'Could not create a zip archive'
        return {'file_path': zipfile}, ''

    success, msg = csv_exporter.export(events=events, pnls=pnls, directory=directory_path)
    if success is False:
        return None, msg

    return True, ''
