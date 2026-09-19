"""Final report rendering (product-spec §23).

Canonical location for the report body; engine.render_report is retained as a
thin re-export for backward compatibility within this codebase.
"""

from __future__ import annotations

from patchcycle.models import ReportData


def render_report(report: ReportData) -> str:
    """Plain-text report body (product-spec §23 format)."""
    outstanding = (
        report.outstanding_updates if report.outstanding_updates is not None else "Unknown"
    )
    health = "Not run"
    if report.health_results:
        health = "Passed" if all(result.ok for result in report.health_results) else "FAILED"
    lines = [
        "D3V PatchCycle",
        "",
        f"Host: {report.hostname}",
        f"OS: {report.os_pretty_name}",
        "",
        f"Result: {report.outcome.value.upper()}",
        "",
        f"Packages available: {report.packages_available}",
        f"Packages upgraded: {report.packages_upgraded}",
        f"Packages held: {report.packages_held}",
        "",
        f"Kernel update: {'Yes' if report.kernel_update else 'No'}",
        f"Reboot required: {'Yes' if report.reboot_required else 'No'}",
        f"Reboot completed: {'Yes' if report.reboot_completed else 'No'}",
        "",
        f"Kernel before: {report.kernel_before}",
        f"Kernel after: {report.kernel_after}",
        "",
        f"Outstanding updates: {outstanding}",
        f"Health checks: {health}",
        f"Failed services: {report.failed_services}",
    ]
    if report.error:
        lines += [
            "",
            f"Stage: {report.error.stage}",
            f"Reason: {report.error.message}",
            f"Manual intervention: {'Required' if report.error.manual_intervention else 'No'}",
        ]
    if report.notification_status:
        lines += ["", "Notifications:"]
        lines += [f"  {name}: {status}" for name, status in report.notification_status.items()]
    if report.warnings:
        lines += ["", "Warnings:", *[f"  {warning}" for warning in report.warnings]]
    for result in report.health_results:
        if not result.ok:
            lines.append(f"Health check {result.name}: {result.detail}")
    return "\n".join(lines) + "\n"
