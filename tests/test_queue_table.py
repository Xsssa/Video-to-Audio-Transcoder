"""
test_queue_table.py - Comprehensive Unit and Integration Tests for QueueTableWidget.

Tests:
1. 8 Standard columns setup and alignment.
2. Sleek monochrome / subtle dim status indicators.
3. Interactive navigation, deletion, reordering (up/down), and details inspection.
4. Batch statistics header and summary footer metrics.
5. Smooth refresh mechanism preserving cursor and scroll offsets.
6. Integration with QueueManager.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.app import App, ComposeResult
from textual.widgets import Label

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from processing.queue_manager import ConversionTask, QueueManager, QueueStats, TaskStatus
from ui.tui_widgets.queue_table import (
    COLUMN_HEADERS,
    COLUMN_KEYS,
    QueueDataTable,
    QueueStatsHeader,
    QueueSummaryFooter,
    QueueTableWidget,
    TaskDeleted,
    TaskDetailModal,
    TaskDetailsRequested,
    TaskMoved,
    TaskSelected,
    format_duration_hms,
    format_file_size,
    format_status_indicator,
    truncate_string,
)


class TestFormattingUtilities(unittest.TestCase):
    """Tests formatting helpers and status indicator styling."""

    def test_format_file_size(self) -> None:
        self.assertEqual(format_file_size(0), "-")
        self.assertEqual(format_file_size(-10), "-")
        self.assertEqual(format_file_size(512), "512 B")
        self.assertEqual(format_file_size(1024), "1.0 KB")
        self.assertEqual(format_file_size(1024 * 1024 * 15), "15.0 MB")
        self.assertEqual(format_file_size(1024 * 1024 * 1024 * 2), "2.0 GB")

    def test_format_duration_hms(self) -> None:
        self.assertEqual(format_duration_hms(None), "--:--:--")
        self.assertEqual(format_duration_hms(-5), "--:--:--")
        self.assertEqual(format_duration_hms(0), "00:00:00")
        self.assertEqual(format_duration_hms(59), "00:00:59")
        self.assertEqual(format_duration_hms(65), "00:01:05")
        self.assertEqual(format_duration_hms(3661), "01:01:01")

    def test_truncate_string(self) -> None:
        self.assertEqual(truncate_string("short.mp4", 20), "short.mp4")
        self.assertEqual(truncate_string("a" * 35, 10), "aaaaaaa...")

    def test_format_status_indicator(self) -> None:
        pending = format_status_indicator(TaskStatus.PENDING)
        self.assertEqual(pending.plain, "[Pending]")
        self.assertEqual(pending.style, "dim #7a7a7a")

        converting = format_status_indicator(TaskStatus.CONVERTING)
        self.assertEqual(converting.plain, "[Converting]")
        self.assertEqual(converting.style, "bold #ffffff")

        completed = format_status_indicator(TaskStatus.COMPLETED)
        self.assertEqual(completed.plain, "[Completed]")
        self.assertEqual(completed.style, "#a8a8a8")

        failed = format_status_indicator(TaskStatus.FAILED)
        self.assertEqual(failed.plain, "[Failed]")
        self.assertEqual(failed.style, "dim #616161")

        paused = format_status_indicator(TaskStatus.PENDING, is_queue_paused=True)
        self.assertEqual(paused.plain, "[Paused]")
        self.assertEqual(paused.style, "dim #7a7a7a")

        probing = format_status_indicator(TaskStatus.PROBING)
        self.assertEqual(probing.plain, "[Probing]")
        self.assertEqual(probing.style, "italic #9e9e9e")

        cancelled = format_status_indicator(TaskStatus.CANCELLED)
        self.assertEqual(cancelled.plain, "[Cancelled]")


class TestQueueTableComponents(unittest.TestCase):
    """Tests QueueDataTable, QueueSummaryFooter, and QueueTableWidget inside Textual App."""

    def _create_sample_tasks(self, count: int = 3) -> list[ConversionTask]:
        tasks = []
        for i in range(count):
            t = ConversionTask(
                task_id=f"task_{i+1}",
                source_file=Path(f"F:/videos/video_{i+1}.mp4"),
                target_format="mp3",
                status=TaskStatus.PENDING,
                input_size_bytes=1024 * 1024 * (i + 1) * 10,
            )
            tasks.append(t)
        return tasks

    def test_columns_and_data_table_initialization(self) -> None:
        class TableApp(App):
            def compose(self) -> ComposeResult:
                yield QueueDataTable(id="queue-data-table")

        app = TableApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                table = app.query_one(QueueDataTable)
                self.assertEqual(table.cursor_type, "row")
                col_keys = [str(k.value) for k in table.columns.keys()]
                self.assertEqual(col_keys, list(COLUMN_KEYS))
                self.assertEqual(len(col_keys), 8)
                self.assertEqual([c.label.plain for c in table.columns.values()], list(COLUMN_HEADERS))

        import asyncio
        asyncio.run(run_test())

    def test_smooth_refresh_in_place_preserves_cursor(self) -> None:
        tasks = self._create_sample_tasks(3)

        class RefreshApp(App):
            def compose(self) -> ComposeResult:
                yield QueueDataTable(id="queue-data-table")

        app = RefreshApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                table = app.query_one(QueueDataTable)
                table.smooth_refresh(tasks)
                self.assertEqual(table.row_count, 3)

                # Move cursor to 2nd row (index 1)
                table.move_cursor(row=1, animate=False)
                self.assertEqual(table.cursor_row, 1)

                # Mutate task 2 without changing order or count
                tasks[1].status = TaskStatus.CONVERTING
                tasks[1].progress = 45.5
                tasks[1].speed = "2.3x"
                tasks[1].eta = "01:15"

                table.smooth_refresh(tasks)

                # Cursor must remain intact at index 1!
                self.assertEqual(table.cursor_row, 1)
                self.assertEqual(table.get_selected_task_id(), "task_2")

                # Verify cell values were updated in-place
                status_cell = table.get_cell("task_2", "col_status")
                self.assertEqual(status_cell.plain, "[Converting]")
                prog_cell = table.get_cell("task_2", "col_progress")
                self.assertIn("45.5%", prog_cell.plain)
                speed_cell = table.get_cell("task_2", "col_speed")
                self.assertEqual(speed_cell.plain, "2.3x")

        import asyncio
        asyncio.run(run_test())

    def test_queue_summary_footer(self) -> None:
        class FooterApp(App):
            def compose(self) -> ComposeResult:
                yield QueueSummaryFooter(id="queue-summary-footer")

        app = FooterApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                footer = app.query_one(QueueSummaryFooter)
                footer.update_stats(
                    staged=10,
                    completed=4,
                    failed=1,
                    runtime_seconds=125,
                    remaining_seconds=250,
                )
                self.assertEqual(footer.staged, 10)
                self.assertEqual(footer.completed, 4)
                self.assertEqual(footer.failed, 1)
                self.assertEqual(footer.runtime_str, "00:02:05")
                self.assertEqual(footer.remaining_str, "00:04:10")

                rendered = footer.render()
                self.assertIn("STAGED:", rendered.plain)
                self.assertIn("COMPLETED:", rendered.plain)
                self.assertIn("FAILED:", rendered.plain)
                self.assertIn("RUNTIME:", rendered.plain)
                self.assertIn("EST. REMAINING:", rendered.plain)

        import asyncio
        asyncio.run(run_test())

    def test_queue_table_widget_actions_reorder_and_delete(self) -> None:
        tasks = self._create_sample_tasks(3)

        class WidgetApp(App):
            def compose(self) -> ComposeResult:
                yield QueueTableWidget(id="queue-widget")

        app = WidgetApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                widget = app.query_one(QueueTableWidget)
                widget.update_tasks(tasks)
                self.assertEqual(widget.row_count, 3)

                # Cursor at 0 initially; move to task_2 (index 1)
                widget.table.move_cursor(row=1, animate=False)
                self.assertEqual(widget.get_selected_task_id(), "task_2")

                # Test move up: task_2 moves from index 1 to index 0
                widget.move_selected_task_up()
                self.assertEqual(widget.get_selected_task_id(), "task_2")
                self.assertEqual(widget.cursor_row, 0)
                self.assertEqual(widget.table._current_task_ids, ["task_2", "task_1", "task_3"])

                # Test move down: task_2 moves from index 0 back to index 1
                widget.move_selected_task_down()
                self.assertEqual(widget.get_selected_task_id(), "task_2")
                self.assertEqual(widget.cursor_row, 1)
                self.assertEqual(widget.table._current_task_ids, ["task_1", "task_2", "task_3"])

                # Test delete: delete task_2
                widget.delete_selected_task()
                self.assertEqual(widget.row_count, 2)
                self.assertEqual(widget.table._current_task_ids, ["task_1", "task_3"])

        import asyncio
        asyncio.run(run_test())

    def test_queue_table_widget_with_queue_manager_integration(self) -> None:
        qm = QueueManager()
        t1 = qm.add_task(Path("sample1.mp4"), "mp3")
        t2 = qm.add_task(Path("sample2.mp4"), "flac")
        t3 = qm.add_task(Path("sample3.mp4"), "wav")

        class ManagerApp(App):
            def compose(self) -> ComposeResult:
                yield QueueTableWidget(queue_manager=qm, id="queue-widget")

        app = ManagerApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                widget = app.query_one(QueueTableWidget)
                self.assertEqual(widget.row_count, 3)

                # Highlight row 2 (t2)
                widget.table.move_cursor(row=1, animate=False)
                self.assertEqual(widget.get_selected_task_id(), t2.task_id)

                # Move t2 up in QueueManager
                widget.move_selected_task_up()
                all_tasks = qm.get_all_tasks()
                self.assertEqual(all_tasks[0].task_id, t2.task_id)
                self.assertEqual(all_tasks[1].task_id, t1.task_id)

                # Delete t1 (now at index 1)
                widget.table.move_cursor(row=1, animate=False)
                widget.delete_selected_task()
                self.assertEqual(len(qm.get_all_tasks()), 2)
                self.assertIsNone(qm.get_task(t1.task_id))

        try:
            import asyncio
            asyncio.run(run_test())
        finally:
            qm.stop()

    def test_task_detail_modal_rendering(self) -> None:
        task = ConversionTask(
            task_id="task_detail_test",
            source_file=Path("F:/test/demo.mp4"),
            target_format="aac",
            status=TaskStatus.CONVERTING,
            progress=67.8,
            speed="3.1x",
            eta="00:42",
            input_size_bytes=1024 * 1024 * 50,
        )

        class ModalApp(App):
            def compose(self) -> ComposeResult:
                yield Label("Root")

        app = ModalApp()

        async def run_test() -> None:
            async with app.run_test() as pilot:
                modal = TaskDetailModal(task)
                app.push_screen(modal)
                content = modal._build_content()
                self.assertIn("task_detail_test", content.plain)
                self.assertIn("demo.mp4", content.plain)
                self.assertIn("AAC", content.plain)
                self.assertIn("67.8%", content.plain)
                self.assertIn("3.1x", content.plain)
                self.assertIn("00:42", content.plain)

        import asyncio
        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
