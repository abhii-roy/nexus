"""Regression coverage for search, undo focus, reordering and recovery."""
import os
from pathlib import Path
import unittest
from unittest import mock

import apsw
import test_keyboard_navigation as navigation
from PyQt6 import QtCore, QtGui, QtWidgets, QtTest
from nexus import graphics, workflow, mainwindow, shortcuts


class WorkflowTests(unittest.TestCase):
    setUpClass = classmethod(navigation.KeyboardNavigationTests.setUpClass.__func__)
    tearDownClass = classmethod(navigation.KeyboardNavigationTests.tearDownClass.__func__)
    setUp = navigation.KeyboardNavigationTests.setUp
    tearDown = navigation.KeyboardNavigationTests.tearDown
    node = navigation.KeyboardNavigationTests.node
    child = navigation.KeyboardNavigationTests.child
    select = navigation.KeyboardNavigationTests.select
    selected = navigation.KeyboardNavigationTests.selected
    key = navigation.KeyboardNavigationTests.key

    def test_undo_deleted_branch_restores_selection_subtree_and_keyboard_focus(self):
        branch = self.child(self.root, 'Branch', -150)
        descendant = self.child(branch, 'Descendant')
        sibling = self.child(self.root, 'Sibling', 150)
        uid, child_uid = branch.node['uid'], descendant.node['uid']
        self.select(branch)
        zoom = self.view.transform()
        self.scene.delete()
        self.assertEqual(self.selected(), [sibling])
        self.scene.undo()
        self.assertEqual([s.node['uid'] for s in self.selected()], [uid])
        self.assertIn(child_uid, [s.node['uid'] for s in self.scene.allChildStems()])
        self.assertTrue(self.view.hasFocus())
        self.assertEqual(self.view.transform(), zoom)
        self.key(QtCore.Qt.Key.Key_Right)
        self.assertEqual(self.selected()[0].node['uid'], child_uid)

    def test_undo_multiple_deleted_branches_restores_base_selection(self):
        one = self.child(self.root, 'One', -150)
        two = self.child(self.root, 'Two', 150)
        self.child(one, 'Nested')
        uids = {one.node['uid'], two.node['uid']}
        self.select(one)
        two.setSelected(True)
        self.scene.delete()
        self.scene.undo()
        self.assertEqual({s.node['uid'] for s in self.selected()}, uids)

    def test_undo_creation_selects_parent_and_no_history_is_noop(self):
        self.graph.clearchanges()
        self.select(self.root)
        self.scene.undo()
        self.assertEqual(self.selected(), [self.root])
        self.key(QtCore.Qt.Key.Key_Tab)
        self.select(self.opened[-1])
        self.scene.undo()
        self.assertEqual(self.selected(), [self.root])
        self.assertEqual(self.root.childStems2, [])

    def test_option_arrow_reorders_persists_and_undo_keeps_selected_subtree(self):
        upper = self.child(self.root, 'Upper', -150)
        lower = self.child(self.root, 'Lower', 150)
        grandchild = self.child(lower, 'Grandchild')
        uid = lower.node['uid']
        self.select(lower)
        before = {s.node['uid']: list(s.node['pos']) for s in (upper, lower)}
        zoom = self.view.transform()
        self.view.verticalScrollBar().setFocus()
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Up,
                               QtCore.Qt.KeyboardModifier.AltModifier | QtCore.Qt.KeyboardModifier.KeypadModifier)
        self.app.sendEvent(self.view.verticalScrollBar(), event)
        self.app.processEvents()
        self.assertLess(lower.leaf.sceneBoundingRect().center().y(), upper.leaf.sceneBoundingRect().center().y())
        self.assertEqual(self.selected(), [lower])
        self.assertIs(grandchild.parentStem(), lower)
        self.assertEqual(self.view.transform(), zoom)
        for s in (upper, lower):
            self.assertEqual(self.graph.getuid(s.node['uid'])['pos'], s.node['pos'])
        self.scene.undo()
        self.assertEqual(self.selected()[0].node['uid'], uid)
        for s in (upper, lower):
            self.assertEqual(s.node['pos'], before[s.node['uid']])

    def test_reordering_boundaries_multiselection_and_modes_are_safe(self):
        upper = self.child(self.root, 'Upper', -150)
        lower = self.child(self.root, 'Lower', 150)
        left = self.child(self.root, 'Left', 0, flip=-1)
        self.select(upper)
        before = self.graph.countchanges()
        self.key(QtCore.Qt.Key.Key_Up, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.graph.countchanges(), before)
        self.key(QtCore.Qt.Key.Key_Down, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(left.node['pos'], [200, 0])
        lower.setSelected(True)
        before = self.graph.countchanges()
        self.key(QtCore.Qt.Key.Key_Down, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.graph.countchanges(), before)
        self.scene.mode = 'presentation'
        self.scene.reorderBranch(1)
        self.scene.undo()
        self.assertEqual(self.graph.countchanges(), before)

    def search_dialog(self):
        owner = QtWidgets.QWidget()
        owner.scene, owner.view = self.scene, self.view
        self.search_owner = owner
        self.dialog = workflow.NodeSearchDialog(owner)
        self.dialog.show()
        self.dialog.search.setFocus()
        self.app.processEvents()
        return self.dialog

    def test_search_html_duplicates_breadcrumbs_and_hidden_ancestors(self):
        branch = self.child(self.root, 'Parent', -150)
        target = self.child(branch, '<b>Quantum</b> photon', 5000)
        other = self.child(self.root, 'Quantum photon', 150)
        uid = target.node['uid']
        branch.openclose.toggleVisibilities()
        self.root.openclose.toggleVisibilities()
        self.select(self.root)
        zoom = self.view.transform()
        dialog = self.search_dialog()
        dialog.search.setText('PHOTON quantum')
        self.assertEqual(dialog.results.count(), 2)
        rows = [dialog.results.item(i) for i in range(2)]
        row = next(i for i, item in enumerate(rows) if item.data(QtCore.Qt.ItemDataRole.UserRole) == uid)
        self.assertIn('Root label › Parent', rows[row].text())
        dialog.results.setCurrentRow(row)
        QtTest.QTest.keyClick(dialog.search, QtCore.Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertEqual([s.node['uid'] for s in self.selected()], [uid])
        self.assertEqual(self.view.transform(), zoom)
        self.assertTrue(self.view.hasFocus())
        # Only the target's path is expanded, not every sibling.
        self.assertIn('hide', self.graph.getuid(other.node['uid']))

    def test_search_empty_no_matches_arrow_choice_and_escape_preserve_selection(self):
        self.child(self.root, 'Match A', -150)
        self.child(self.root, 'Match B', 150)
        self.select(self.root)
        dialog = self.search_dialog()
        self.assertEqual(dialog.results.count(), 0)
        dialog.search.setText('not present')
        self.assertEqual(dialog.results.count(), 0)
        QtTest.QTest.keyClick(dialog.search, QtCore.Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        dialog.search.setText('Match')
        QtTest.QTest.keyClick(dialog.search, QtCore.Qt.Key.Key_Down)
        self.assertEqual(dialog.results.currentRow(), 1)
        QtTest.QTest.keyClick(dialog.search, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(self.selected(), [self.root])

    def test_search_background_translucent_and_heading_draggable(self):
        dialog = self.search_dialog()
        self.assertEqual(dialog.windowModality(), QtCore.Qt.WindowModality.NonModal)
        self.assertTrue(dialog.windowFlags() & QtCore.Qt.WindowType.Tool)
        image = dialog.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        scale = image.devicePixelRatio()
        alpha = image.pixelColor(int(10 * scale), int(100 * scale)).alpha()
        self.assertGreater(alpha, 180)
        self.assertLess(alpha, 245)
        before = dialog.pos()
        origin = dialog.heading.rect().center()
        QtTest.QTest.mousePress(dialog.heading, QtCore.Qt.MouseButton.LeftButton, pos=origin)
        event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(origin),
            QtCore.QPointF(dialog.heading.mapToGlobal(origin) + QtCore.QPoint(20, 15)),
            QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(dialog.heading, event)
        QtTest.QTest.mouseRelease(dialog.heading, QtCore.Qt.MouseButton.LeftButton, pos=origin)
        self.assertEqual(dialog.pos(), before + QtCore.QPoint(20, 15))
        if os.environ.get('NEXUS_SEARCH_PREVIEW'):
            dialog.grab().save(os.environ['NEXUS_SEARCH_PREVIEW'])

    def test_snapshots_are_valid_bounded_skip_unchanged_and_recover_without_overwrite(self):
        store = workflow.SnapshotStore(self.graph, Path(self.tmp.name) / 'recovery', keep=2)
        first = store.capture()
        self.assertTrue(first.exists())
        self.assertIsNone(store.capture())
        self.root.node['test_value'] = 'first change'
        self.root.node.save()
        second = store.capture()
        self.root.node['test_value'] = 'later change'
        self.root.node.save()
        third = store.capture()
        self.assertEqual(set(store.snapshots()), {second, third})
        self.assertFalse(first.exists())
        copied = Path(self.tmp.name) / 'recovered.nex'
        store.recover_copy(second, copied)
        connection = apsw.Connection(str(copied), flags=apsw.SQLITE_OPEN_READONLY)
        try:
            self.assertEqual(connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            value = connection.execute("SELECT json_extract(data,'$.test_value') FROM nodes WHERE uid=?",
                                       [self.root.node['uid']]).fetchone()[0]
            self.assertEqual(value, 'first change')
        finally:
            connection.close()
        for existing in (copied, Path(self.graph.path), second):
            with self.assertRaises(FileExistsError):
                store.recover_copy(second, existing)
        self.assertEqual(self.graph.getuid(self.root.node['uid'])['test_value'], 'later change')
        with self.assertRaises(ValueError):
            store.recover_copy(copied, Path(self.tmp.name) / 'invalid.nex')

    def test_snapshot_failure_retries_and_does_not_prune_old_backup(self):
        store = workflow.SnapshotStore(self.graph, Path(self.tmp.name) / 'recovery')
        first = store.capture()
        self.root.node['test_value'] = 'new'
        self.root.node.save()
        with mock.patch('nexus.workflow.apsw.Connection', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                store.capture()
        self.assertEqual(store.snapshots(), [first])
        self.assertIsNotNone(store.capture())
        self.assertEqual(list(store.directory.glob('*.partial')), [])

    def test_recovery_timer_and_dialog_recover_a_new_copy(self):
        owner = QtWidgets.QWidget()
        owner.scene, owner.view = self.scene, self.view
        messages, opened, seen, errors = [], [], [], []
        owner.showMessage = lambda *args: messages.append(args)
        store_class = workflow.SnapshotStore
        destination = Path(self.tmp.name) / 'dialog-recovered.nex'
        with mock.patch('nexus.workflow.SnapshotStore', side_effect=lambda graph:
                        store_class(graph, Path(self.tmp.name) / 'recovery')), \
             mock.patch.object(self.app, 'raiseOrOpen', opened.append, create=True), \
             mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
            manager = workflow.RecoveryManager(owner)
            self.assertEqual(manager.timer.interval(), 300000)
            self.root.node['test_value'] = 'timer change'
            self.root.node.save()
            manager.timer.timeout.emit()
            self.assertEqual(len(manager.store.snapshots()), 2)
            manager.timer.timeout.emit()
            self.assertEqual(len(manager.store.snapshots()), 2)

            def interact():
                dialog = self.app.activeModalWidget()
                try:
                    seen.append(dialog.findChild(QtWidgets.QListWidget).count())
                    button = next(b for b in dialog.findChildren(QtWidgets.QPushButton)
                                  if b.text() == 'Recover Copy…')
                    button.click()
                except Exception as error:
                    errors.append(error)
                finally:
                    if dialog is not None and dialog.isVisible():
                        dialog.reject()

            try:
                QtCore.QTimer.singleShot(0, interact)
                manager.showSnapshots()
                self.assertEqual(errors, [])
                self.assertEqual(seen, [2])
                self.assertEqual(opened, [str(destination)])
                self.assertTrue(destination.exists())
                self.assertTrue(Path(self.graph.path).exists())
                self.assertEqual(messages, [])
            finally:
                manager.timer.stop()
                owner.deleteLater()
                self.app.processEvents()

    def test_mainwindow_find_undo_shortcuts_and_recovery_timer_are_wired(self):
        # The application services normally come from NexusApplication. Stub
        # only those services; create the real window and deliver real shortcuts.
        self.graph.savesetting('version', graphics.VERSION)
        QtCore.QSettings('Ectropy', 'Nexus').setValue('contentsVisible', True)
        store_class = workflow.SnapshotStore
        with mock.patch.object(self.app, 'updateWindowMenu', lambda: None, create=True), \
             mock.patch.object(self.app, 'createViewImage', lambda view: None, create=True), \
             mock.patch.object(self.app, 'dialogOpen', lambda: None, create=True), \
             mock.patch.object(self.app, 'toggleStreaminServer', lambda: None, create=True), \
             mock.patch.object(self.app, 'streaming', False, create=True), \
             mock.patch.object(self.app, 'windowMenu', QtWidgets.QMenu('Window'), create=True), \
             mock.patch('nexus.workflow.SnapshotStore', side_effect=lambda graph:
                        store_class(graph, Path(self.tmp.name) / 'recovery')):
            window = mainwindow.MainWindow(self.graph.path)
            try:
                window.show()
                window.activateWindow()
                window.view.setFocus()
                self.app.processEvents()
                self.assertTrue(window.contentsDock.isVisible())
                self.assertIn(window.childSizeAct, window.editMenu.actions())
                self.assertEqual(window.childSizeButton.defaultAction(), window.childSizeAct)
                self.assertTrue(any(row[2] == 'Child size…' for row in shortcuts.shortcut_rows(window)))
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_R,
                                     QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.app.processEvents()
                self.assertTrue(window._childSizeDialog.isVisible())
                window._childSizeDialog.choosePreset(2)
                window._childSizeDialog.applyButton.click()
                self.app.processEvents()
                self.assertEqual(window.scene.graph.fetch('[r:Root]').one.get('child_size_ratio'), 1.0)
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_Z, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.app.processEvents()
                self.assertNotIn('child_size_ratio', window.scene.graph.fetch('[r:Root]').one)
                # Toolbar click remains available while typing and saves text
                # before opening the ratio chooser.
                root = window.scene.root()
                root.setSelected(True)
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_Return)
                self.app.processEvents()
                self.assertIsNotNone(window.view.inline.item)
                QtTest.QTest.keyClicks(window.view, ' saved')
                typing = window.view.inline.item
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_R,
                                     QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.app.processEvents()
                self.assertIs(window.view.inline.item, typing)
                self.assertFalse(window._childSizeDialog.isVisible())
                window.childSizeButton.click()
                self.app.processEvents()
                self.assertIsNone(window.view.inline.item)
                self.assertTrue(window._childSizeDialog.isVisible())
                window._childSizeDialog.reject()
                self.app.processEvents()
                self.assertIn(window.contentsAct, window.viewMenu.actions())
                self.assertIn(window.scene.root().node['uid'], window.contentsPanel.items)
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_O,
                                     QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.app.processEvents()
                self.assertTrue(window.contentsDock.isHidden())
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_O,
                                     QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.app.processEvents()
                self.assertTrue(window.contentsDock.isVisible())
                self.assertTrue(any(row[2] == 'Contents' for row in shortcuts.shortcut_rows(window)))
                self.assertTrue(window.recovery.timer.isActive())
                self.assertEqual(len(window.recovery.store.snapshots()), 1)
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.app.processEvents()
                self.assertTrue(window._nodeSearchDialog.isVisible())
                panel = window._nodeSearchDialog
                panel.search.setText('Root')
                previous_result = panel.results.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)
                panel.activateWindow()
                panel.search.setFocus()
                self.app.processEvents()
                QtTest.QTest.keyClick(panel.search, QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.app.processEvents()
                self.assertFalse(panel.isVisible())
                self.assertTrue(window.view.hasFocus())
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.app.processEvents()
                self.assertTrue(window._nodeSearchDialog.isVisible())
                self.assertIs(window._nodeSearchDialog, panel)
                self.assertEqual(panel.search.text(), 'Root')
                self.assertEqual(panel.results.currentItem().data(QtCore.Qt.ItemDataRole.UserRole), previous_result)
                window.findNode()
                self.assertFalse(window._nodeSearchDialog.isVisible())
                root = window.scene.root()
                root.setSelected(True)
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_Tab)
                self.app.processEvents()
                self.assertIsNotNone(window.view.inline.item)
                QtTest.QTest.keyClicks(window.view, 'New node')
                active = window.view.inline.item
                cursor = active.textCursor()
                cursor.select(QtGui.QTextCursor.SelectionType.Document)
                active.setTextCursor(cursor)
                window.deleteAct.trigger()
                self.assertEqual(active.toPlainText(), '')
                self.assertEqual(len(window.scene.allChildStems()), 2)
                window.undoAct.trigger()
                self.assertEqual(active.toPlainText(), 'New node')
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_Escape)
                uid = window.scene.selectedItems()[0].node['uid']
                window.activateWindow()
                window.view.setFocus()
                self.app.processEvents()
                delete_key = (QtCore.Qt.Key.Key_Backspace if QtGui.QKeySequence.keyBindings(
                              QtGui.QKeySequence.StandardKey.Backspace) else QtCore.Qt.Key.Key_Delete)
                QtTest.QTest.keyClick(window.view, delete_key)
                self.app.processEvents()
                self.assertEqual([s.node['uid'] for s in window.scene.selectedItems()], [root.node['uid']])
                QtTest.QTest.keyClick(window.view, QtCore.Qt.Key.Key_Z, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.app.processEvents()
                self.assertEqual([s.node['uid'] for s in window.scene.selectedItems()], [uid])
                self.assertTrue(any('Move the selected branch' in row[2] for row in shortcuts.shortcut_rows(window)))
            finally:
                window.editDialog.hide()
                window.editDialog.deleteLater()
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
