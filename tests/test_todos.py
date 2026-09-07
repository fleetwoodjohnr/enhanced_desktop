from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_forge.providers import todos


class TodoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = str(Path(self.temporary.name) / "todos.json")
        self.store_patch = patch.object(todos, "STORE_PATH", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def test_full_crud_and_status_projection(self) -> None:
        with self.assertRaises(ValueError):
            todos.add("   ")
        first = todos.add("First task")
        second = todos.add("Second task")

        self.assertTrue(todos.update(first["id"], text="Renamed", status="blocked"))
        self.assertTrue(todos.update(second["id"], status="done"))
        self.assertFalse(todos.update("missing", status="todo"))

        state = todos.TodosProvider().fetch({})
        self.assertEqual([item["text"] for item in state["items"]], ["Renamed", "Second task"])
        self.assertEqual(state["counts"]["blocked"], 1)
        self.assertEqual(state["counts"]["done"], 1)

        self.assertTrue(todos.remove(first["id"]))
        self.assertFalse(todos.remove("missing"))
        self.assertEqual([item["id"] for item in todos.load()], [second["id"]])

    def test_drag_order_is_saved_as_canonical_array_order(self) -> None:
        ids = [todos.add(label)["id"] for label in ("A", "B", "C", "D")]

        # The target is expressed after removing the dragged item, matching
        # the GNOME DND target's contract.
        self.assertTrue(todos.move(ids[1], 3))
        self.assertEqual([item["text"] for item in todos.load()], ["A", "C", "D", "B"])
        self.assertTrue(todos.move(ids[3], 0))
        self.assertEqual([item["text"] for item in todos.load()], ["D", "A", "C", "B"])

        # A new provider instance must see the exact persisted manual order.
        projected = todos.TodosProvider().fetch({})["items"]
        self.assertEqual([item["text"] for item in projected], ["D", "A", "C", "B"])


if __name__ == "__main__":
    unittest.main()
