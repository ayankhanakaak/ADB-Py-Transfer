'''
App Name: ADB Py-Transfer
Version: 3.0.2
Completion Date: 25 July, 2026
'''

import sys
import os
import subprocess
import re
import shutil
try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QListWidget, QLabel, QMessageBox,
                             QMainWindow, QTreeView, QFileSystemModel, QSplitter,
                             QProgressDialog, QAbstractItemView, QHeaderView, QDesktopWidget,
                             QListWidgetItem, QCheckBox, QStyle, QFileIconProvider,
                             QDialog, QTabWidget, QLineEdit, QMenu, QShortcut, QInputDialog, QSpinBox)
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QIcon, QKeySequence
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDir, QModelIndex, QSortFilterProxyModel, QFileInfo, QTimer
from datetime import datetime

class AdbLogger:
    ENABLED = False
    
    @staticmethod
    def log(command, output):
        if not AdbLogger.ENABLED:
            return
        try:
            with open("adb_log.txt", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}]\nCommand: {command}\nOutput:\n{output.strip()}\n{'-'*40}\n")
        except Exception:
            pass

class DirectoryFirstProxyModel(QSortFilterProxyModel):
    def lessThan(self, left_index, right_index):
        source_model = self.sourceModel()
        left_item = source_model.itemFromIndex(left_index.siblingAtColumn(0))
        right_item = source_model.itemFromIndex(right_index.siblingAtColumn(0))
        
        left_name = left_item.text() if left_item else ""
        right_name = right_item.text() if right_item else ""
        
        # Keep ".." always pinned at the top
        if left_name == "..": return self.sortOrder() == Qt.AscendingOrder
        if right_name == "..": return self.sortOrder() == Qt.DescendingOrder

        left_is_dir = left_item.data(Qt.UserRole) == "dir" if left_item else False
        right_is_dir = right_item.data(Qt.UserRole) == "dir" if right_item else False

        # Group folders above files
        if left_is_dir != right_is_dir:
            return left_is_dir if self.sortOrder() == Qt.AscendingOrder else right_is_dir

        # Correctly sort Size column (index 1) numerically instead of alphabetically
        if left_index.column() == 1:
            l_text = source_model.data(left_index)
            r_text = source_model.data(right_index)
            l_val = int(l_text) if l_text and l_text.isdigit() else -1
            r_val = int(r_text) if r_text and r_text.isdigit() else -1
            return l_val < r_val

        return super().lessThan(left_index, right_index)

def get_android_item_icon(file_name, is_dir=False):
    provider = QFileIconProvider()

    if is_dir:
        return provider.icon(QFileIconProvider.Folder)

    return provider.icon(QFileInfo(file_name))

def get_startupinfo():
    """Hides the console window for subprocesses on Windows."""
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo

class SizePollingThread(QThread):
    progress = pyqtSignal(int, str)
    debug = pyqtSignal(str, str)

    def __init__(self, check_func, total_transfer_size, completed_bytes, label, interval_ms=1000):
        super().__init__()
        self.check_func = check_func
        self.total_transfer_size = total_transfer_size
        self.completed_bytes = completed_bytes
        self.label = label
        self.interval_ms = interval_ms
        self.running = True
        self.last_percent = -1

    def run(self):
        while self.running:
            try:
                current_dest_size = self.check_func()
                total_current = self.completed_bytes + current_dest_size
                
                if self.total_transfer_size > 0:
                    percent = int((total_current / self.total_transfer_size) * 100)
                else:
                    percent = 100
                    
                percent = max(0, min(percent, 100))

                if percent != self.last_percent:
                    self.last_percent = percent
                    self.progress.emit(percent, self.label)
            except Exception as e:
                self.debug.emit("Size polling error", str(e))

            self.msleep(self.interval_ms)

    def stop(self):
        self.running = False
        self.quit()
        self.wait()


class TransferWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, device_id, direction, transfers, action='copy', batch_size=50):
        super().__init__()
        self.device_id = device_id
        self.direction = direction
        self.transfers = transfers
        self.action = action
        self.batch_size = batch_size
        self.poller = None
        self.process = None

    def cancel(self):
        self.requestInterruption()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def stop_poller(self):
        if self.poller:
            self.poller.stop()
            self.poller = None

    def get_remote_sizes(self, remote_paths):
        total_size = 0
        sizes_dict = {}
        chunk_size = 50
        
        for i in range(0, len(remote_paths), chunk_size):
            chunk = remote_paths[i:i+chunk_size]
            cmd = ['adb', '-s', self.device_id, 'shell', 'stat', '-c', '%s'] + [f'"{p}"' for p in chunk]
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
            
            lines = result.stdout.strip().splitlines()
            for j, path in enumerate(chunk):
                try:
                    size = int(lines[j].strip())
                    sizes_dict[path] = size
                    total_size += size
                except Exception:
                    sizes_dict[path] = 0
                    
        return total_size, sizes_dict

    def run_transfer_command(self, cmd):
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            startupinfo=get_startupinfo()
        )

        output_lines = []
        while True:
            if self.isInterruptionRequested():
                self.cancel()
                raise RuntimeError("Transfer cancelled.")

            line = self.process.stdout.readline()
            if line:
                output_lines.append(line.rstrip())
            elif self.process.poll() is not None:
                break

        returncode = self.process.wait()
        output = "\n".join(output_lines)
        AdbLogger.log(" ".join(cmd), output)

        if returncode != 0:
            raise RuntimeError(output or f"adb transfer failed with exit code {returncode}")

    def run(self):
        try:
            total_files = len(self.transfers)
            if total_files == 0:
                self.finished.emit(True, "No files to transfer.")
                return

            self.progress.emit(0, "Calculating total transfer size...")
            
            total_transfer_size = 0
            file_sizes = {}
            
            if self.direction == 'push':
                for local, remote in self.transfers:
                    sz = os.path.getsize(local) if os.path.exists(local) else 0
                    file_sizes[local] = sz
                    total_transfer_size += sz
            else:
                total_transfer_size, file_sizes = self.get_remote_sizes([t[1] for t in self.transfers])

            completed_bytes = 0
            chunk_size = self.batch_size if self.batch_size > 1 else 1

            for i in range(0, total_files, chunk_size):
                if self.isInterruptionRequested():
                    raise RuntimeError("Transfer cancelled.")

                chunk = self.transfers[i:i+chunk_size]
                chunk_total_size = sum(file_sizes[t[0] if self.direction == 'push' else t[1]] for t in chunk)

                if chunk_size == 1:
                    name = os.path.basename(chunk[0][0] if self.direction == 'push' else chunk[0][1])
                    label = f"({i+1}/{total_files}) {self.direction.capitalize()}ing:\n{name}"
                else:
                    label = f"Bulk {self.direction.capitalize()}ing...\nFiles {i+1} to {min(i+chunk_size, total_files)} of {total_files}"

                def check_destination_size(current_chunk=chunk):
                    current_sz = 0
                    if self.direction == 'push':
                        _, remote_sz_dict = self.get_remote_sizes([t[1] for t in current_chunk])
                        current_sz = sum(remote_sz_dict.values())
                    else:
                        for t in current_chunk:
                            local_dest = t[0]
                            current_sz += os.path.getsize(local_dest) if os.path.exists(local_dest) else 0
                    return current_sz

                self.progress.emit(int((completed_bytes / max(1, total_transfer_size)) * 100), label)

                self.poller = SizePollingThread(check_destination_size, total_transfer_size, completed_bytes, label)
                self.poller.progress.connect(self.progress)
                self.poller.debug.connect(AdbLogger.log)
                self.poller.start()

                if self.direction == 'push':
                    if chunk_size == 1:
                        cmd = ['adb', '-s', self.device_id, 'push', chunk[0][0], chunk[0][1]]
                    else:
                        local_paths = [t[0] for t in chunk]
                        remote_dir = os.path.dirname(chunk[0][1])
                        if not remote_dir.endswith('/'): remote_dir += '/'
                        cmd = ['adb', '-s', self.device_id, 'push'] + local_paths + [remote_dir]
                else:
                    if chunk_size == 1:
                        cmd = ['adb', '-s', self.device_id, 'pull', chunk[0][1], chunk[0][0]]
                    else:
                        remote_srcs = [t[1] for t in chunk]
                        local_dir = os.path.dirname(chunk[0][0])
                        cmd = ['adb', '-s', self.device_id, 'pull'] + remote_srcs + [local_dir]

                AdbLogger.log(" ".join(cmd), "Transfer Started")
                self.run_transfer_command(cmd)
                self.stop_poller()

                completed_bytes += chunk_total_size

            # Delete source files if this was a CUT action and wasn't cancelled
            if self.action == 'cut' and not self.isInterruptionRequested():
                for local_path, remote_path in self.transfers:
                    if self.direction == 'push':
                        # Windows requires paths to be normalized with backslashes for native delete
                        norm_path = os.path.normpath(local_path)
                        if send2trash:
                            try: send2trash(norm_path)
                            except: pass
                        else:
                            try:
                                if os.path.isdir(norm_path): shutil.rmtree(norm_path, ignore_errors=True)
                                else: os.remove(norm_path)
                            except: pass
                    else:
                        cmd = ['adb', '-s', self.device_id, 'shell', 'rm', '-rf', f'"{remote_path}"']
                        subprocess.run(cmd, startupinfo=get_startupinfo())

            self.progress.emit(100, f"{self.direction.capitalize()} completed.")
            self.finished.emit(True, "Transfer(s) completed successfully.")

        except Exception as e:
            self.stop_poller()
            AdbLogger.log("Transfer Error", str(e))
            self.finished.emit(False, str(e))


class FileManager(QMainWindow):
    closed = pyqtSignal()

    def __init__(self, device_id, device_name):
        super().__init__()
        self.device_id = device_id
        self.device_name = device_name
        self.current_remote_path = "/sdcard/"
        self.initUI()
        self.load_remote_directory(self.current_remote_path)

    def initUI(self):
        self.setWindowTitle(f"ADB File Commander - {self.device_name}")
        self.resize(1000, 600)
        self.center()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # Transfer Speed Selection
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Transfer Speed (Files at once):"))
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 9999)
        self.spin_batch.setValue(50)
        top_bar.addWidget(self.spin_batch)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left Pane - Local PC
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        local_header_layout = QHBoxLayout()
        local_header_layout.addWidget(QLabel("Local PC"))
        
        # 3. Toggle for hidden files
        self.chk_hidden = QCheckBox("Show Hidden Files")
        self.chk_hidden.stateChanged.connect(self.toggle_hidden_files)
        local_header_layout.addWidget(self.chk_hidden, alignment=Qt.AlignRight)
        left_layout.addLayout(local_header_layout)
        
        self.local_model = QFileSystemModel()
        self.local_model.setRootPath('') 
        self.local_model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs) # Default: No hidden
        
        self.local_tree = QTreeView()
        self.local_tree.setModel(self.local_model)
        # 2. Empty root index to show drives (C:, D:, etc.)
        self.local_tree.setRootIndex(self.local_model.index(''))
        self.local_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_tree.setSortingEnabled(True)
        self.local_tree.sortByColumn(0, Qt.AscendingOrder)
        self.local_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_tree.customContextMenuRequested.connect(self.show_local_menu)
        left_layout.addWidget(self.local_tree)

        # Center Buttons
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        self.btn_push_copy = QPushButton("Copy ->")
        self.btn_push_cut = QPushButton("Cut ->")
        self.btn_pull_copy = QPushButton("<- Copy")
        self.btn_pull_cut = QPushButton("<- Cut")
        
        self.btn_push_copy.clicked.connect(lambda: self.do_transfer_btn('push', 'copy'))
        self.btn_push_cut.clicked.connect(lambda: self.do_transfer_btn('push', 'cut'))
        self.btn_pull_copy.clicked.connect(lambda: self.do_transfer_btn('pull', 'copy'))
        self.btn_pull_cut.clicked.connect(lambda: self.do_transfer_btn('pull', 'cut'))

        center_layout.addStretch()
        center_layout.addWidget(self.btn_push_copy)
        center_layout.addWidget(self.btn_push_cut)
        center_layout.addWidget(self.btn_pull_copy)
        center_layout.addWidget(self.btn_pull_cut)
        center_layout.addStretch()

        # Right Pane - Remote Android
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        self.remote_path_label = QLabel(f"Remote Device: {self.current_remote_path}")
        right_layout.addWidget(self.remote_path_label)

        self.remote_model = QStandardItemModel()
        self.remote_model.setHorizontalHeaderLabels(['Name', 'Size', 'Date', 'Permissions'])
        
        # Apply the Custom Proxy Model for intelligent sorting
        self.proxy_model = DirectoryFirstProxyModel()
        self.proxy_model.setSourceModel(self.remote_model)
        
        self.remote_tree = QTreeView()
        self.remote_tree.setModel(self.proxy_model)
        self.remote_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_tree.doubleClicked.connect(self.remote_double_clicked)
        self.remote_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_tree.setSortingEnabled(True)
        self.remote_tree.sortByColumn(0, Qt.AscendingOrder)
        self.remote_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_tree.customContextMenuRequested.connect(self.show_remote_menu)
        
        self.local_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.remote_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        right_layout.addWidget(self.remote_tree)

        # Clipboard State and Global Shortcuts
        self.clipboard = {'side': None, 'action': None, 'paths': [], 'remote_dir': None}
        QShortcut(QKeySequence("Ctrl+C"), self, self.action_copy)
        QShortcut(QKeySequence("Ctrl+X"), self, self.action_cut)
        QShortcut(QKeySequence("Ctrl+V"), self, self.action_paste)
        QShortcut(QKeySequence("Del"), self, self.action_delete)
        QShortcut(QKeySequence("F2"), self, self.action_rename)

        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([450, 100, 450])

    def center(self):
        qr = self.frameGeometry()
        cp = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

    def toggle_hidden_files(self, state):
        base_filter = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs
        if state == Qt.Checked:
            self.local_model.setFilter(base_filter | QDir.Hidden | QDir.System)
        else:
            self.local_model.setFilter(base_filter)

    def run_adb_shell(self, cmd_string):
        cmd = ['adb', '-s', self.device_id, 'shell', cmd_string]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
            AdbLogger.log(" ".join(cmd), result.stdout)
            return result.stdout, result.returncode
        except Exception as e:
            AdbLogger.log(" ".join(cmd), f"ERROR: {str(e)}")
            return "", -1

    def load_remote_directory(self, path):
        self.remote_model.removeRows(0, self.remote_model.rowCount())
        self.current_remote_path = path
        self.remote_path_label.setText(f"Remote Device: {self.current_remote_path}")

        stdout, code = self.run_adb_shell(f'ls -laL "{path}"')
        
        if code != 0 or "Permission denied" in stdout:
            QMessageBox.warning(self, "Error", f"Cannot read directory: {path}\nPermission denied.")
            if path != "/sdcard/":
                self.load_remote_directory("/sdcard/")
            return

        if path != "/":
            up_item = QStandardItem("..")
            up_item.setData("dir", Qt.UserRole)
            up_item.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
            self.remote_model.appendRow([up_item, QStandardItem(""), QStandardItem(""), QStandardItem("")])

        lines = stdout.strip().split('\n')
        for line in lines:
            if not line or line.startswith('total '): continue
            
            parts = line.split(maxsplit=7)
            if len(parts) < 8: continue
            
            perms, links, owner, group, size, date, time, name = parts
            
            if name in ('.', '..'): continue

            is_dir = perms.startswith('d')
            
            name_item = QStandardItem(name)
            name_item.setData("dir" if is_dir else "file", Qt.UserRole)
            
            name_item.setIcon(get_android_item_icon(name, is_dir))
            
            size_item = QStandardItem(size if not is_dir else "")
            date_item = QStandardItem(f"{date} {time}")
            perm_item = QStandardItem(perms)

            self.remote_model.appendRow([name_item, size_item, date_item, perm_item])

    def remote_double_clicked(self, index):
        # Map the clicked index through the Proxy Model back to the true Source Model
        source_index = self.proxy_model.mapToSource(index)
        item = self.remote_model.item(source_index.row(), 0)
        item_type = item.data(Qt.UserRole)
        name = item.text()

        if name == "..":
            new_path = os.path.dirname(self.current_remote_path.rstrip('/'))
            if not new_path: new_path = "/"
            if not new_path.endswith('/'): new_path += "/"
            self.load_remote_directory(new_path)
        elif item_type == "dir":
            new_path = f"{self.current_remote_path}{name}/"
            self.load_remote_directory(new_path)

    def get_selected_local_paths(self):
        paths = []
        for index in self.local_tree.selectionModel().selectedRows():
            if index.isValid():
                paths.append(self.local_model.filePath(index))
        return paths

    def get_selected_remote_names(self):
        names = []
        for index in self.remote_tree.selectionModel().selectedRows():
            if index.isValid():
                source_index = self.proxy_model.mapToSource(index)
                names.append(self.remote_model.item(source_index.row(), 0).text())
        return names

    def center_transfer_dialog(self):
        if not hasattr(self, "progress_dialog") or self.progress_dialog is None:
            return

        parent_rect = self.geometry()
        dialog_rect = self.progress_dialog.frameGeometry()
        dialog_rect.moveCenter(parent_rect.center())
        self.progress_dialog.move(dialog_rect.topLeft())

    def update_transfer_progress(self, value, text):
        if not hasattr(self, "progress_dialog") or self.progress_dialog is None:
            return

        # Store local reference so it cannot become None mid-execution
        dialog = self.progress_dialog

        try:
            # Set text FIRST, then value (since setValue can trigger event loops)
            dialog.setLabelText(text)
            dialog.setValue(value)
            
            if self.progress_dialog is not None:
                QApplication.processEvents()
                self.center_transfer_dialog()
        except RuntimeError:
            # Catch C++ object deletion errors safely
            pass

    def start_transfer(self, direction, transfers, action='copy', batch_size=50):
        self.progress_dialog = QProgressDialog(f"{direction.capitalize()}ing...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("File Transfer")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.setMinimumDuration(0)

        self.worker = TransferWorker(self.device_id, direction, transfers, action, batch_size)
        self.worker.progress.connect(self.update_transfer_progress)
        self.worker.finished.connect(self.on_transfer_finished)
        self.progress_dialog.canceled.connect(self.worker.cancel)

        self.progress_dialog.show()
        self.center_transfer_dialog()
        self.worker.start()

    def do_transfer_btn(self, direction, action):
        batch_size = self.spin_batch.value()
        if direction == 'push':
            local_paths = self.get_selected_local_paths()
            if not local_paths:
                QMessageBox.information(self, "Info", "Select valid files from the Local PC to push.")
                return
            transfers = []
            for path in local_paths:
                if os.path.exists(path):
                    remote_path = f"{self.current_remote_path}{os.path.basename(path)}"
                    transfers.append((path, remote_path))
            if transfers:
                self.start_transfer('push', transfers, action, batch_size)
                
        elif direction == 'pull':
            remote_names = self.get_selected_remote_names()
            if not remote_names or ".." in remote_names:
                QMessageBox.information(self, "Info", "Select valid files from the Remote Device to pull.")
                return
            index = self.local_tree.currentIndex()
            if not index.isValid() or self.local_model.filePath(index) == "":
                QMessageBox.information(self, "Info", "Select a valid destination directory on the Local PC.")
                return
            local_dir = self.local_model.filePath(index) if self.local_model.isDir(index) else os.path.dirname(self.local_model.filePath(index))
            transfers = []
            for name in remote_names:
                remote_path = f"{self.current_remote_path}{name}"
                local_path = os.path.join(local_dir, name)
                transfers.append((local_path, remote_path))
            if transfers:
                self.start_transfer('pull', transfers, action, batch_size)

    def _set_clipboard(self, action):
        focused = QApplication.focusWidget()
        if focused == self.local_tree:
            paths = self.get_selected_local_paths()
            if paths:
                self.clipboard = {'side': 'local', 'action': action, 'paths': paths, 'remote_dir': None}
        elif focused == self.remote_tree:
            names = self.get_selected_remote_names()
            if names and ".." not in names:
                self.clipboard = {'side': 'remote', 'action': action, 'paths': names, 'remote_dir': self.current_remote_path}

    def action_copy(self):
        self._set_clipboard('copy')

    def action_cut(self):
        self._set_clipboard('cut')

    def action_paste(self):
        if not self.clipboard['paths']: return
        
        focused = QApplication.focusWidget()
        target_side = 'local' if focused == self.local_tree else 'remote' if focused == self.remote_tree else None
        if not target_side: return
        
        action = self.clipboard['action']
        source_side = self.clipboard['side']
        paths = self.clipboard['paths']
        batch_size = self.spin_batch.value()
        
        if source_side == 'local' and target_side == 'remote':
            transfers = []
            for path in paths:
                if os.path.exists(path):
                    remote_path = f"{self.current_remote_path}{os.path.basename(path)}"
                    transfers.append((path, remote_path))
            if transfers:
                self.start_transfer('push', transfers, action, batch_size)
                if action == 'cut': self.clipboard['paths'] = []
                
        elif source_side == 'remote' and target_side == 'local':
            index = self.local_tree.currentIndex()
            if not index.isValid() or self.local_model.filePath(index) == "":
                QMessageBox.information(self, "Info", "Select a destination directory on the Local PC.")
                return
            local_dir = self.local_model.filePath(index) if self.local_model.isDir(index) else os.path.dirname(self.local_model.filePath(index))
            transfers = []
            for name in paths:
                remote_path = f"{self.clipboard['remote_dir']}{name}"
                local_path = os.path.join(local_dir, name)
                transfers.append((local_path, remote_path))
            if transfers:
                self.start_transfer('pull', transfers, action, batch_size)
                if action == 'cut': self.clipboard['paths'] = []
                
        elif source_side == 'local' and target_side == 'local':
            index = self.local_tree.currentIndex()
            if not index.isValid() or self.local_model.filePath(index) == "": return
            local_dir = self.local_model.filePath(index) if self.local_model.isDir(index) else os.path.dirname(self.local_model.filePath(index))
            for path in paths:
                if not os.path.exists(path): continue
                target = os.path.join(local_dir, os.path.basename(path))
                if path == target: continue
                if action == 'copy':
                    if os.path.isdir(path): shutil.copytree(path, target)
                    else: shutil.copy2(path, target)
                else:
                    shutil.move(path, target)
            if action == 'cut': self.clipboard['paths'] = []
            
        elif source_side == 'remote' and target_side == 'remote':
            for name in paths:
                old_path = f"{self.clipboard['remote_dir']}{name}"
                new_path = f"{self.current_remote_path}{name}"
                if old_path == new_path: continue
                cmd_verb = 'cp -r' if action == 'copy' else 'mv'
                cmd = ['adb', '-s', self.device_id, 'shell', cmd_verb, f'"{old_path}"', f'"{new_path}"']
                subprocess.run(cmd, startupinfo=get_startupinfo())
            if action == 'cut': self.clipboard['paths'] = []
            self.load_remote_directory(self.current_remote_path)

    def action_delete(self):
        focused = QApplication.focusWidget()
        if focused == self.local_tree:
            paths = self.get_selected_local_paths()
            if not paths: return
            reply = QMessageBox.question(self, "Confirm Delete", f"Delete {len(paths)} local file(s)?\n(Will use Recycle Bin if 'send2trash' is installed)", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                for path in paths:
                    # Windows requires paths to be normalized with backslashes for native delete
                    norm_path = os.path.normpath(path)
                    if send2trash:
                        try: send2trash(norm_path)
                        except: pass
                    else:
                        try:
                            if os.path.isdir(norm_path): shutil.rmtree(norm_path, ignore_errors=True)
                            else: os.remove(norm_path)
                        except: pass
        elif focused == self.remote_tree:
            names = self.get_selected_remote_names()
            if not names or ".." in names: return
            reply = QMessageBox.question(self, "Confirm Delete", f"Permanently delete {len(names)} remote file(s)?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                for name in names:
                    path = f"{self.current_remote_path}{name}"
                    cmd = ['adb', '-s', self.device_id, 'shell', 'rm', '-rf', f'"{path}"']
                    subprocess.run(cmd, startupinfo=get_startupinfo())
                self.load_remote_directory(self.current_remote_path)

    def action_rename(self):
        focused = QApplication.focusWidget()
        if focused == self.local_tree:
            paths = self.get_selected_local_paths()
            if not paths: return
            
            current_name = os.path.basename(paths[0])
            new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", QLineEdit.Normal, current_name)
            if not ok or not new_name.strip(): return
            new_name = new_name.strip()
            
            for i, path in enumerate(paths):
                dir_name = os.path.dirname(path)
                old_ext = os.path.splitext(path)[1]
                target_name = new_name if "." in new_name else new_name + old_ext
                if len(paths) > 1:
                    base = os.path.splitext(target_name)[0]
                    ext = os.path.splitext(target_name)[1]
                    target_name = f"{base}_{i+1}{ext}"
                target = os.path.join(dir_name, target_name)
                try: os.rename(path, target)
                except: pass
                
        elif focused == self.remote_tree:
            names = self.get_selected_remote_names()
            if not names or ".." in names: return
            
            current_name = names[0]
            new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", QLineEdit.Normal, current_name)
            if not ok or not new_name.strip(): return
            new_name = new_name.strip()
            
            for i, name in enumerate(names):
                old_ext = os.path.splitext(name)[1]
                target_name = new_name if "." in new_name else new_name + old_ext
                if len(names) > 1:
                    base = os.path.splitext(target_name)[0]
                    ext = os.path.splitext(target_name)[1]
                    target_name = f"{base}_{i+1}{ext}"
                old_path = f"{self.current_remote_path}{name}"
                new_path = f"{self.current_remote_path}{target_name}"
                cmd = ['adb', '-s', self.device_id, 'shell', 'mv', f'"{old_path}"', f'"{new_path}"']
                subprocess.run(cmd, startupinfo=get_startupinfo())
            self.load_remote_directory(self.current_remote_path)

    def show_local_menu(self, pos):
        menu = QMenu()
        menu.addAction("Copy (Ctrl+C)", self.action_copy)
        menu.addAction("Cut (Ctrl+X)", self.action_cut)
        menu.addAction("Paste (Ctrl+V)", self.action_paste)
        menu.addSeparator()
        menu.addAction("Rename (F2)", self.action_rename)
        menu.addAction("Delete (Del)", self.action_delete)
        menu.exec_(self.local_tree.viewport().mapToGlobal(pos))

    def show_remote_menu(self, pos):
        menu = QMenu()
        menu.addAction("Copy (Ctrl+C)", self.action_copy)
        menu.addAction("Cut (Ctrl+X)", self.action_cut)
        menu.addAction("Paste (Ctrl+V)", self.action_paste)
        menu.addSeparator()
        menu.addAction("Rename (F2)", self.action_rename)
        menu.addAction("Delete (Del)", self.action_delete)
        menu.exec_(self.remote_tree.viewport().mapToGlobal(pos))

    def on_transfer_finished(self, success, message):
        if hasattr(self, "worker") and self.worker is not None:
            try:
                self.worker.progress.disconnect(self.update_transfer_progress)
            except Exception:
                pass

        if hasattr(self, 'progress_dialog') and self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog = None

        if success:
            self.load_remote_directory(self.current_remote_path)
        else:
            QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class MdnsScannerThread(QThread):
    found = pyqtSignal(list, list)

    def run(self):
        while not self.isInterruptionRequested():
            try:
                cmd = ['adb', 'mdns', 'services']
                res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo(), timeout=2)
                connects, pairs = [], []
                
                for line in res.stdout.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('List'): continue
                    
                    match = re.search(r'\b(\d{1,3}(?:\.\d{1,3}){3}:\d+)\b', line)
                    if match:
                        ip_port = match.group(1)
                        if 'pair' in line.lower():
                            pairs.append(ip_port)
                        else:
                            connects.append(ip_port)
                            
                self.found.emit(connects, pairs)
            except Exception:
                pass
            self.msleep(1000)

class WirelessDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Wireless Device")
        self.resize(400, 350)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tab_connect = QWidget()
        self.tab_pair = QWidget()
        self.tabs.addTab(self.tab_connect, "ADB Connect")
        self.tabs.addTab(self.tab_pair, "ADB Pair")
        layout.addWidget(self.tabs)

        # ADB Connect Tab
        conn_layout = QVBoxLayout(self.tab_connect)
        conn_layout.addWidget(QLabel("Scanning for devices on network..."))
        self.list_connect = QListWidget()
        self.list_connect.itemClicked.connect(self.on_connect_item_clicked)
        self.list_connect.itemDoubleClicked.connect(self.on_connect_double_clicked)
        conn_layout.addWidget(self.list_connect)

        row_c = QHBoxLayout()
        self.inp_conn_ip = QLineEdit()
        self.inp_conn_ip.setPlaceholderText("IP:PORT")
        self.inp_conn_ip.returnPressed.connect(self.do_connect)
        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self.do_connect)
        row_c.addWidget(self.inp_conn_ip)
        row_c.addWidget(self.btn_conn)
        conn_layout.addLayout(row_c)

        # ADB Pair Tab
        pair_layout = QVBoxLayout(self.tab_pair)
        pair_layout.addWidget(QLabel("Scanning for pairable devices..."))
        self.list_pair = QListWidget()
        self.list_pair.itemClicked.connect(self.on_pair_item_clicked)
        pair_layout.addWidget(self.list_pair)

        row_p1 = QHBoxLayout()
        self.inp_pair_ip = QLineEdit()
        self.inp_pair_ip.setPlaceholderText("IP:PORT")
        row_p1.addWidget(self.inp_pair_ip)

        row_p2 = QHBoxLayout()
        self.inp_pair_code = QLineEdit()
        self.inp_pair_code.setPlaceholderText("Pairing Code")
        
        # Shift focus to Pairing Code input when Enter is pressed on IP input
        self.inp_pair_ip.returnPressed.connect(self.inp_pair_code.setFocus)
        
        self.inp_pair_code.returnPressed.connect(self.do_pair)
        self.btn_pair = QPushButton("Pair")
        self.btn_pair.clicked.connect(self.do_pair)
        row_p2.addWidget(self.inp_pair_code)
        row_p2.addWidget(self.btn_pair)

        pair_layout.addLayout(row_p1)
        pair_layout.addLayout(row_p2)

        self.scanner = MdnsScannerThread(self)
        self.scanner.found.connect(self.update_lists)
        self.scanner.start()

    def stop_scanner(self):
        if hasattr(self, 'scanner') and self.scanner.isRunning():
            self.scanner.requestInterruption()
            self.scanner.wait()

    def accept(self):
        self.stop_scanner()
        super().accept()

    def reject(self):
        self.stop_scanner()
        super().reject()

    def closeEvent(self, event):
        self.stop_scanner()
        super().closeEvent(event)

    def on_connect_item_clicked(self, item):
        self.inp_conn_ip.setText(item.text())

    def on_connect_double_clicked(self, item):
        self.inp_conn_ip.setText(item.text())
        self.do_connect()

    def on_pair_item_clicked(self, item):
        self.inp_pair_ip.setText(item.text())

    def update_lists(self, connects, pairs):
        current_c = [self.list_connect.item(i).text() for i in range(self.list_connect.count())]
        if set(current_c) != set(connects):
            self.list_connect.clear()
            self.list_connect.addItems(connects)

        current_p = [self.list_pair.item(i).text() for i in range(self.list_pair.count())]
        if set(current_p) != set(pairs):
            self.list_pair.clear()
            self.list_pair.addItems(pairs)

    def do_connect(self):
        ip_port = self.inp_conn_ip.text().strip()
        if not ip_port: return
        
        cmd = ['adb', 'connect', ip_port]
        res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
        out = (res.stdout + "\n" + res.stderr).strip()
        AdbLogger.log(" ".join(cmd), out)
        
        if "connected to" in out.lower() or "already connected" in out.lower():
            self.accept()
        else:
            QMessageBox.critical(self, "Connection Failed", f"Failed to connect to {ip_port}.\n\nOutput:\n{out}")

    def do_pair(self):
        ip_port = self.inp_pair_ip.text().strip()
        code = self.inp_pair_code.text().strip()
        if not ip_port or not code: return
        
        cmd = ['adb', 'pair', ip_port, code]
        res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
        out = (res.stdout + "\n" + res.stderr).strip()
        AdbLogger.log(" ".join(cmd), out)
        
        if "successfully paired" in out.lower():
            QMessageBox.information(self, "Success", "Paired successfully.\nYou can now Connect.")
            self.tabs.setCurrentIndex(0)
            self.inp_conn_ip.setText(ip_port)
        else:
            QMessageBox.critical(self, "Connection Failed", f"Failed to pair with {ip_port}.\n\nOutput:\n{out}")

class DeviceSelector(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('ADB Device Selector V.3.0.2')
        self.resize(350, 400)
        
        layout = QVBoxLayout()
        
        self.label = QLabel("Searching for ADB devices...")
        layout.addWidget(self.label)
        
        self.device_list = QListWidget()
        self.device_list.itemSelectionChanged.connect(self.check_selection)
        # Route double-click through the Continue button so it respects the enabled/disabled state
        self.device_list.itemDoubleClicked.connect(lambda item: self.btn_continue.click())
        layout.addWidget(self.device_list)
        
        self.btn_wireless = QPushButton("Add Wireless Device")
        self.btn_wireless.clicked.connect(self.open_wireless_dialog)
        layout.addWidget(self.btn_wireless)
        
        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        
        self.btn_continue = QPushButton("Continue")
        self.btn_continue.setEnabled(False)
        self.btn_continue.clicked.connect(self.launch_file_manager)
        
        # Add Enter/Return shortcuts to trigger the Continue button
        QShortcut(QKeySequence("Return"), self, self.btn_continue.click)
        QShortcut(QKeySequence("Enter"), self, self.btn_continue.click)
        
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_continue)
        
        self.chk_log = QCheckBox("Enable ADB Logging (adb_log.txt)")
        self.chk_log.stateChanged.connect(self.toggle_logging)
        
        layout.addLayout(btn_layout)
        layout.addWidget(self.chk_log)
        self.setLayout(layout)
        
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_devices)
        self.refresh_timer.start(1000)
        
        self.refresh_devices()

    def hideEvent(self, event):
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.start()
        super().showEvent(event)

    def closeEvent(self, event):
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        super().closeEvent(event)

    def open_wireless_dialog(self):
        dlg = WirelessDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh_devices()

    def toggle_logging(self, state):
        AdbLogger.ENABLED = (state == Qt.Checked)

    def refresh_devices(self):
        selected_dev_id = None
        if self.device_list.selectedItems():
            selected_dev_id = self.device_list.selectedItems()[0].data(Qt.UserRole)

        self.device_list.clear()
        self.btn_continue.setEnabled(False)
        
        try:
            cmd = ['adb', 'devices']
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
            AdbLogger.log(" ".join(cmd), result.stdout)
            lines = result.stdout.strip().split('\n')
            
            devices_found = 0
            accessible_devices = 0
            
            for line in lines[1:]:
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) == 2:
                        dev_id, status = parts
                        
                        if status == 'device':
                            accessible_devices += 1
                            model_cmd = ['adb', '-s', dev_id, 'shell', 'getprop', 'ro.product.model']
                            try:
                                model_result = subprocess.run(model_cmd, capture_output=True, text=True, startupinfo=get_startupinfo(), timeout=2)
                                AdbLogger.log(" ".join(model_cmd), model_result.stdout)
                                model_name = model_result.stdout.strip()
                            except Exception as e:
                                AdbLogger.log(" ".join(model_cmd), f"ERROR: {str(e)}")
                                model_name = ""
                            
                            display_name = f"{model_name} [{dev_id}]" if model_name else f"Unknown Device [{dev_id}]"
                        else:
                            display_name = f"[{dev_id}] ({status})"
                            
                        item = QListWidgetItem(display_name)
                        item.setData(Qt.UserRole, dev_id)
                        item.setData(Qt.UserRole + 1, status)
                        self.device_list.addItem(item)
                        
                        if dev_id == selected_dev_id:
                            item.setSelected(True)
                            
                        devices_found += 1
                        
            if devices_found == 0:
                self.label.setText("No devices found. Ensure USB debugging is ON.")
            else:
                self.label.setText(f"Found {devices_found} device(s).")
                
            if accessible_devices == 0:
                self.refresh_timer.setInterval(1000)
            else:
                self.refresh_timer.setInterval(5000)
                
        except FileNotFoundError as e:
            AdbLogger.log("adb devices", f"ERROR: ADB not found in PATH. {str(e)}")
            self.label.setText("ADB not found. Is it added to your system PATH?")
            self.refresh_timer.stop()
            QMessageBox.critical(self, "ADB Error", "Could not find ADB executable.\nPlease install ADB and add it to PATH.")

    def check_selection(self):
        items = self.device_list.selectedItems()
        if items and items[0].data(Qt.UserRole + 1) == "device":
            self.btn_continue.setEnabled(True)
        else:
            self.btn_continue.setEnabled(False)

    def launch_file_manager(self):
        selected_item = self.device_list.selectedItems()[0]
        device_id = selected_item.data(Qt.UserRole)
        device_name = selected_item.text().split(' [')[0] # Extract friendly name for title
        
        self.hide()
        self.fm = FileManager(device_id, device_name)
        self.fm.closed.connect(self.show)
        self.fm.show()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    selector = DeviceSelector()
    selector.show()
    sys.exit(app.exec_())
