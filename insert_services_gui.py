from PySide6 import QtCore, QtWidgets, QtGui
from typing import Any, Callable, Optional, NamedTuple, List, Tuple, Dict, Union
import os, time, traceback, itertools
import json
import pandas as pd
import psycopg2

from database_properties import Properties
import adding_functional_objects

class ColorizingLine(QtWidgets.QLineEdit):
    def __init__(self, callback: Callable[[Optional[QtWidgets.QLineEdit], Optional[str]], None], text: Optional[str] = None, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._state: str = text or ''
        self.setText(text or '')
        self._callback = callback
    
    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:
        self._state = self.text()
        return super().focusInEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:
        if self.text() != self._state:
            if self.isVisible():
                self._callback(self, self._state)
            self._state = self.text()
        return super().focusOutEvent(event)


class ColorizingComboBox(QtWidgets.QComboBox):
    def __init__(self, callback: Callable[[Optional[QtWidgets.QComboBox]], None], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._callback = callback
        self._state = 0
        self.currentIndexChanged.connect(self.changeEvent)

    # def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
    #     super().wheelEvent(event)
    #     if self.currentIndex() != self._state:
    #         if self.isVisible():
    #             self._callback(self)
    #         self._state = self.currentIndex()
    
    def changeEvent(self, *_):
        if self._state != self.currentIndex():
            old_state = self._state
            self._state = self.currentIndex()
            if self.isVisible():
                self._callback(self, old_state)



class CheckableTableView(QtWidgets.QTableView):

    colorTable = NamedTuple('ColorTable', [
            ('on', QtGui.QColor),
            ('off', QtGui.QColor)
    ])(QtGui.QColor(152, 224, 173), QtGui.QColor(248, 161, 164)) # type: ignore
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.columnAt(int(event.position().x())) == 0:
            self.toggle_row(self.rowAt(int(event.position().y())))
        else:
            return super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self.editTriggers() == QtWidgets.QTableWidget.NoEditTriggers:
            return super().keyPressEvent(event)
        key = event.key()
        indexes = set(map(lambda index: index.row(), filter(lambda index: index.column() == 0, self.selectedIndexes())))
        if len(indexes) > 0:
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Minus, QtCore.Qt.Key_Plus):
                func = self.turn_row_off if key == QtCore.Qt.Key_Minus else self.turn_row_on if key == QtCore.Qt.Key_Plus else self.toggle_row
                for row in indexes:
                    func(row)
        else:
            return super().keyPressEvent(event)

    def toggle_row(self, row: int) -> None:
        item_index = self.model().index(row, 0)
        item = self.model().data(item_index)
        self.model().setData(item_index, '-' if item == '+' else '+')
        self.model().setData(item_index, CheckableTableView.colorTable.off if item == '+' else CheckableTableView.colorTable.on,
                QtCore.Qt.BackgroundRole)

    def turn_row_on(self, row: int) -> None:
        item_index = self.model().index(row, 0)
        self.model().setData(item_index, '+')
        self.model().setData(item_index, CheckableTableView.colorTable.on, QtCore.Qt.BackgroundRole)

    def turn_row_off(self, row: int) -> None:
        item_index = self.model().index(row, 0)
        self.model().setData(item_index, '-')
        self.model().setData(item_index, CheckableTableView.colorTable.off, QtCore.Qt.BackgroundRole)

    def is_turned_on(self, row: int) -> bool:
        return self.model().itemData(self.model().index(row, 0)) == '+'


class DropPushButton(QtWidgets.QPushButton):
    def __init__(self, text: str, formats: List[str], callback: Callable[[str], None], parent: Optional[QtWidgets.QWidget] = None):
        self.formats = tuple((f'.{format}' for format in formats))
        self._callback = callback
        super().__init__(text, parent=parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().text().startswith('file:///') and event.mimeData().text().endswith(self.formats):
            event.setDropAction(QtCore.Qt.LinkAction)
            event.accept()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._callback(event.mimeData().text()[len('file:///'):])


MainWindowDefaultValues = NamedTuple('MainWindowDefaultValues', [
        ('db_address', str),
        ('db_port', int),
        ('db_name', str),
        ('db_user', str),
        ('db_pass', str),
        ('service_type', str),
        ('service_code', str),
        ('city_function', str),
        ('min_capacity', str),
        ('max_capacity', str),
        ('min_status', str),
        ('max_status', str),
        ('latitude', str),
        ('longitude', str),
        ('address', str),
        ('name', str),
        ('opening_hours', str),
        ('website', str),
        ('phone', str),
        ('osm_id', str),
    ]
)

def get_main_window_default_values() -> MainWindowDefaultValues:
    return MainWindowDefaultValues('127.0.0.1', 5432, 'city_db_final', 'kanu', 'postgres', '', '', '', '', '', '', '', 'x', 'y',
            'yand_adr', 'name', 'opening_hours', 'contact:website', 'contact:phone', 'id')

def get_main_window_default_address_prefixes() -> List[str]:
    return ['Россия, Санкт-Петербург']

def get_default_city_functions() -> List[str]:
    return ['(необходимо соединение с базой)']

def get_default_object_classes() -> List[str]:
    return ['(необходимо соединение с базой)']

def get_default_service_types() -> List[str]:
    return ['(необходимо соединение с базой)']

    
class MainWindow(QtWidgets.QWidget):

    DatabaseFields = NamedTuple('DatabaseFields', [
            ('address', QtWidgets.QLineEdit),
            ('name', QtWidgets.QLineEdit),
            ('user', QtWidgets.QLineEdit),
            ('password', QtWidgets.QLineEdit)
        ]
    )

    InsertionOptionsFields = NamedTuple('InsertionOptionsFields', [
            ('service_type', QtWidgets.QLineEdit),
            ('service_code', QtWidgets.QLineEdit),
            ('min_capacity', QtWidgets.QLineEdit),
            ('max_capacity', QtWidgets.QLineEdit),
            ('min_status', QtWidgets.QLineEdit),
            ('max_status', QtWidgets.QLineEdit),
            ('city_function', QtWidgets.QComboBox),
            ('service_type_choose', QtWidgets.QComboBox),
            ('service_type_choosable', QtWidgets.QCheckBox),
            ('is_building', QtWidgets.QCheckBox)
        ]
    )

    DocumentFields = NamedTuple('DocumentFields', [
            ('latitude', QtWidgets.QComboBox),
            ('longitude', QtWidgets.QComboBox),
            ('address', QtWidgets.QComboBox),
            ('name', QtWidgets.QComboBox),
            ('opening_hours', QtWidgets.QComboBox),
            ('website', QtWidgets.QComboBox),
            ('phone', QtWidgets.QComboBox),
            ('osm_id', QtWidgets.QComboBox),
            ('capacity', QtWidgets.QComboBox)
        ]
    )

    colorTable = NamedTuple('ColorTable', [
            ('light_green', QtGui.QColor),
            ('light_red', QtGui.QColor),
            ('dark_green', QtGui.QColor),
            ('dark_red', QtGui.QColor),
            ('grey', QtGui.QColor),
            ('sky_blue', QtGui.QColor)
    ])(QtGui.QColor(200, 239, 212), QtGui.QColor(255, 192, 203), QtGui.QColor(97, 204, 128), \
            QtGui.QColor(243, 104, 109), QtGui.QColor(230, 230, 230), QtGui.QColor(148, 216, 246)) # type: ignore

    default_values = get_main_window_default_values()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        self._db_properties = Properties(MainWindow.default_values.db_address, MainWindow.default_values.db_port,
                MainWindow.default_values.db_name, MainWindow.default_values.db_user, MainWindow.default_values.db_pass)
        super().__init__(parent)

        self._is_options_ok = False
        self._is_document_ok = False

        self._layout = QtWidgets.QHBoxLayout()
        self._left = QtWidgets.QVBoxLayout()
        self._right_scroll = QtWidgets.QScrollArea()
        self._right_scroll_widget = QtWidgets.QWidget()
        self._right_scroll_vlayout = QtWidgets.QVBoxLayout()
        self._right = QtWidgets.QVBoxLayout()

        self.setLayout(self._layout)
        self._layout.addLayout(self._left)
        self._right_scroll_widget.setLayout(self._right)
        self._right_scroll.setWidget(self._right_scroll_widget)
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll_vlayout.addWidget(self._right_scroll)
        self._right_scroll_widget.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
        self._layout.addWidget(self._right_scroll)

        self._open_file_btn = DropPushButton('Открыть файл', ['xlsx', 'xls', 'json', 'geojson', 'ods', 'csv'], self.on_open_file)
        self._open_file_btn.clicked.connect(self.on_open_file)
        self._load_objects_btn = QtWidgets.QPushButton('Загрузить сервисы')
        self._load_objects_btn.setStyleSheet('font-weight: bold')
        self._load_objects_btn.clicked.connect(self.on_load_objects)
        self._load_objects_btn.setVisible(False)
        self._save_results_btn = QtWidgets.QPushButton('Сохранить результаты')
        self._save_results_btn.setStyleSheet('font-weight: bold')
        self._save_results_btn.clicked.connect(self.on_save_results)
        self._save_results_btn.setVisible(False)
        left_hlayout = QtWidgets.QHBoxLayout()
        left_hlayout.addWidget(self._open_file_btn)
        left_hlayout.addWidget(self._load_objects_btn)
        left_hlayout.addWidget(self._save_results_btn)
        self._left.addLayout(left_hlayout)
        self._left.setAlignment(QtCore.Qt.AlignCenter)
        self._table: Optional[QtWidgets.QTableView] = None

        self._db_group_box = QtWidgets.QGroupBox('База данных')
        self._db_group = QtWidgets.QFormLayout()
        self._db_group_box.setLayout(self._db_group)
        self._database_fields = MainWindow.DatabaseFields(*(QtWidgets.QLineEdit() for _ in range(4)))
        self._database_fields.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self._db_group.addRow('Адрес:', self._database_fields.address)
        self._db_group.addRow('База:', self._database_fields.name)
        self._db_group.addRow('Пользователь:', self._database_fields.user)
        self._db_group.addRow('Пароль:', self._database_fields.password)
        self._db_check_btn = QtWidgets.QPushButton('Проверить подключение')
        self._db_check_btn.clicked.connect(self.on_connection_check)
        self._db_check_res = QtWidgets.QLabel('?')
        self._db_group.addRow(self._db_check_btn, self._db_check_res)
        self._right.addWidget(self._db_group_box)

        self._options_group_box = QtWidgets.QGroupBox('Опции вставки')
        self._options_group = QtWidgets.QFormLayout()
        self._options_group_box.setLayout(self._options_group)
        self._options_fields = MainWindow.InsertionOptionsFields(
                ColorizingLine(self.on_options_change),
                ColorizingLine(self.on_options_change),
                ColorizingLine(self.on_options_change),
                ColorizingLine(self.on_options_change),
                ColorizingLine(self.on_options_change),
                ColorizingLine(self.on_options_change),
                ColorizingComboBox(self.on_options_change),
                ColorizingComboBox(self.on_options_change),
                QtWidgets.QCheckBox(),
                QtWidgets.QCheckBox()
        )
        self._options_fields.service_type_choosable.clicked.connect(lambda: self.on_choose_change(self._options_fields.service_type_choosable))
        self._options_fields.service_type_choose.addItems(get_default_service_types())
        self._options_fields.service_type_choose.setEnabled(False)
        self._options_fields.service_type_choose.view().setMinimumWidth(len(max(get_default_service_types(), key=len)) * 8)

        self._options_group.addRow('Тип сервиса:', self._options_fields.service_type)
        self._options_group.addRow('Выбрать тип сервиса:', self._options_fields.service_type_choosable)
        self._options_group.addRow('Код сервиса:', self._options_fields.service_code)
        self._options_group.addRow('Городская функция:', self._options_fields.city_function)
        self._options_group.addRow('Минимальная мощность:', self._options_fields.min_capacity)
        self._options_group.addRow('Максимальная мощность:', self._options_fields.max_capacity)
        self._options_group.addRow('Минимальный статус:', self._options_fields.min_status)
        self._options_group.addRow('Максимальный статус:', self._options_fields.max_status)
        self._options_group.addRow('Сервис-здание?:', self._options_fields.is_building)
        self._right.addWidget(self._options_group_box)

        self._document_group_box = QtWidgets.QGroupBox('Сопоставление документа')
        self._document_group = QtWidgets.QFormLayout()
        self._document_group_box.setLayout(self._document_group)
        self._document_fields = MainWindow.DocumentFields(*(ColorizingComboBox(self.on_document_change) for _ in range(9)))
        self._document_address_prefixes = [ColorizingLine(self.on_prefix_check) for _ in range(len(get_main_window_default_address_prefixes()))]
        self._document_group.addRow('Широта:', self._document_fields.latitude)
        self._document_group.addRow('Долгота:', self._document_fields.longitude)
        self._document_group.addRow('Адрес:', self._document_fields.address)
        self._document_group.addRow('Название:', self._document_fields.name)
        self._document_group.addRow('Рабочие часы:', self._document_fields.opening_hours)
        self._document_group.addRow('Веб-сайт:', self._document_fields.website)
        self._document_group.addRow('Телефон:', self._document_fields.phone)
        self._document_group.addRow('OSM id:', self._document_fields.osm_id)
        self._document_group.addRow('Мощность:', self._document_fields.capacity)
        self._right.addWidget(self._document_group_box)

        self._prefixes_group_box = QtWidgets.QGroupBox('Префиксы адреса')
        self._prefixes_group = QtWidgets.QVBoxLayout()
        self._prefixes_group_box.setLayout(self._prefixes_group)
        for prefix in self._document_address_prefixes:
            self._prefixes_group.addWidget(prefix)
        self._address_prefix_add_btn = QtWidgets.QPushButton('Добавить префикс')
        self._address_prefix_add_btn.clicked.connect(self.on_prefix_add)
        self._address_prefix_remove_btn = QtWidgets.QPushButton('Удалить префикс')
        self._address_prefix_remove_btn.clicked.connect(self.on_prefix_remove)
        self._address_prefix_remove_btn.setEnabled(False)
        self._prefixes_group.addWidget(self._address_prefix_add_btn)
        self._prefixes_group.addWidget(self._address_prefix_remove_btn)
        self._prefixes_group.addWidget(QtWidgets.QLabel('Новый префикс'))
        self._prefixes_group.addWidget(QtWidgets.QLineEdit())
        self._right.addWidget(self._prefixes_group_box)
        
        types: Optional[Dict[str, Tuple[str, str]]]
        if os.path.isfile('types.json'):
            with open('types.json', 'rt', encoding='utf-8') as f:
                types = json.load(f)
            types = dict(map(lambda x: (x[0].lower(), x[1]), types.items())) # type: ignore
        else:
            types = None

        self._right.setAlignment(QtCore.Qt.AlignTop)
        right_width = max(map(lambda box: box.sizeHint().width(), (self._db_group_box, self._options_group_box,
                self._document_group_box, self._prefixes_group_box)))
        
        self._right_scroll.setFixedWidth(int(right_width * 1.15))
        self._db_group_box.setFixedWidth(right_width)
        self._options_group_box.setFixedWidth(right_width)
        self._document_group_box.setFixedWidth(right_width)
        self._prefixes_group_box.setFixedWidth(right_width)

        self._database_fields.address.setText(f'{MainWindow.default_values.db_address}:{MainWindow.default_values.db_port}')
        self._database_fields.name.setText(MainWindow.default_values.db_name)
        self._database_fields.user.setText(MainWindow.default_values.db_user)
        self._database_fields.password.setText(MainWindow.default_values.db_pass)
        
        self._options_fields.service_type.setText(MainWindow.default_values.service_type)
        self._options_fields.service_code.setText(MainWindow.default_values.service_code)
        self._options_fields.city_function.addItems(get_default_city_functions())
        self._options_fields.city_function.view().setMinimumWidth(len(max(get_default_city_functions(), key=len)) * 8)
        self._options_fields.city_function.setEnabled(False)
        self._options_fields.min_capacity.setText(MainWindow.default_values.min_capacity)
        self._options_fields.max_capacity.setText(MainWindow.default_values.max_capacity)

        for field in self._document_fields:
            field.addItem('(необходимо открыть файл)')
            field.setEnabled(False)
        for line, prefix_line in zip(self._document_address_prefixes, get_main_window_default_address_prefixes()):
            line.setText(prefix_line)
            line.setMinimumWidth(250)

        self._service_type_params: Dict[str, Tuple[str, int, int, int, int, bool, str]] = {}
        
        self.on_options_change()

    def on_open_file(self, filepath: Optional[str] = None) -> None:
        if not filepath:
            try:
                fileDialog = QtWidgets.QFileDialog(self)
                fileDialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
                fileDialog.setNameFilters(('All files (*.xlsx *.xls *.json *.geojson *.ods *.csv)', 'Modern Excel files (*.xlsx)',
                        'Excel files (*.xls *.ods)', 'GeoJSON files (*.json *.geojson)', 'CSV files (*.csv)'))
                if fileDialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                filename = fileDialog.selectedFiles()[0]
            except ValueError:
                QtWidgets.QMessageBox.critical(self, 'Невозможно открыть файл', 'Ошибка при открытии файла')
                return
            except Exception as ex:
                QtWidgets.QMessageBox.critical(self, 'Невозможно открыть файл', f'Неизвестная ошибка при открытии: {ex}')
                return
        else:
            filename = filepath

        df = adding_functional_objects.load_objects(filename)
        self.setWindowTitle(f'Загрузка объектов - "{filename[filename.rindex("/") + 1:]}"')

        self._table_axes: List[str] = ['Загрузить'] + list(df.axes[1])
        field: QtWidgets.QComboBox
        for field in self._document_fields: # type: ignore
            previous_text = field.currentText()
            field.clear()
            field.addItem('-')
            field.addItems(self._table_axes[1:])
            if previous_text in self._table_axes:
                field.setCurrentIndex(self._table_axes.index(previous_text))
            field.setEnabled(True)
        for field, default_value in zip(self._document_fields, MainWindow.default_values[12:]):
            if field.currentIndex() == 0 and default_value in self._table_axes:
                field.setCurrentIndex(self._table_axes.index(default_value))
        self._table_model = QtGui.QStandardItemModel(*df.shape)
        self._table_model.setHorizontalHeaderLabels(list(self._table_axes))
        for i, service in df.iterrows():
            for j, data in enumerate(service, 1):
                self._table_model.setItem(i, j, QtGui.QStandardItem(str(data or '')))
            ok_item = QtGui.QStandardItem('+')
            ok_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self._table_model.setItem(i, 0, ok_item)
            self._table_model.setData(self._table_model.index(i, 0), CheckableTableView.colorTable.on, QtCore.Qt.BackgroundRole)

        if self._table is None:
            self._table = CheckableTableView()
            self._left.insertWidget(0, self._table)

        self._load_objects_btn.setVisible(True)
        self._save_results_btn.setVisible(False)

        self.on_document_change()
        self.on_prefix_check()

        self._table.setModel(self._table_model)
        self._table.setEditTriggers(QtWidgets.QTableWidget.DoubleClicked)
        self._table.horizontalHeader().setMinimumSectionSize(0)
        self._table.resizeColumnsToContents()

    def table_as_DataFrame(self, include_all: bool = True) -> pd.DataFrame:
        lines: List[List[Any]] = []
        index: List[int] = []
        for row in range(self._table_model.rowCount()):
            if include_all or self._table_model.index(row, 0).data() == '+':
                lines.append([])
                index.append(row)
                lines[-1].append('1' if self._table_model.index(row, 0).data() == '+' else '0')
                for col in range(self._table_model.columnCount())[1:]:
                    lines[-1].append(self._table_model.index(row, col).data())
        df = pd.DataFrame(lines, columns=self._table_axes, index=index)
        return df

    def on_load_objects(self) -> None:
        self._load_objects_btn.setEnabled(False)
        app.setOverrideCursor(QtCore.Qt.BusyCursor)
        is_commit = not bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
        try:
            service_type_id = adding_functional_objects.ensure_service_type(self._db_properties.conn,
                    self._options_fields.service_type.text() if not self._options_fields.service_type_choosable.isChecked() else \
                             self._options_fields.service_type_choose.currentText(),
                    self._options_fields.service_code.text() if not self._options_fields.service_type_choosable.isChecked() else \
                             None,
                    (int(self._options_fields.min_capacity.text()) if self._options_fields.min_capacity.text() not in ('', '-') else None) \
                            if not self._options_fields.service_type_choosable.isChecked() else None,
                    (int(self._options_fields.max_capacity.text()) if self._options_fields.max_capacity.text() not in ('', '-') else None) \
                            if not self._options_fields.service_type_choosable.isChecked() else None,
                    (int(self._options_fields.min_status.text()) if self._options_fields.min_status.text() not in ('', '-') else None) \
                            if not self._options_fields.service_type_choosable.isChecked() else None,
                    (int(self._options_fields.max_status.text()) if self._options_fields.max_status.text() not in ('', '-') else None) \
                            if not self._options_fields.service_type_choosable.isChecked() else None,
                    self._options_fields.city_function.currentText(),
                    self._options_fields.is_building.isChecked(),
                    is_commit)
            results = adding_functional_objects.add_objects(
                    self._db_properties.conn,
                    self.table_as_DataFrame(False),
                    self._options_fields.service_type.text() if not self._options_fields.service_type_choosable.isChecked() else \
                             self._options_fields.service_type_choose.currentText(),
                    service_type_id,
                    adding_functional_objects.initInsertionMapping(
                        self._document_fields.name.currentText(),
                        self._document_fields.opening_hours.currentText(),
                        self._document_fields.website.currentText(),
                        self._document_fields.phone.currentText(),
                        self._document_fields.address.currentText(),
                        self._document_fields.osm_id.currentText(),
                        None,
                        self._document_fields.latitude.currentText(),
                        self._document_fields.longitude.currentText()
                    ),
                    list(map(lambda line_edit: line_edit.text(), self._document_address_prefixes)),
                    self._prefixes_group.itemAt(self._prefixes_group.count() - 1).widget().text(),
                    self._options_fields.is_building.isChecked(),
                    is_commit,
            )
            if not is_commit:
                self._db_properties.conn.rollback()
        except psycopg2.OperationalError as ex:
            QtWidgets.QMessageBox.critical(self, 'Ошибка при загрузке',
                    f'Произошла ошибка при загрузке объектов в базу\nВозможны проблемы с подключением к базе')
            self._db_check_btn.click()
            return
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, 'Ошибка при загрузке', f'Произошла ошибка при загрузке объектов в базу\n{ex}')
            traceback.print_exc()
            return
        finally:
            self._load_objects_btn.setEnabled(True)
            app.restoreOverrideCursor()
        df = self.table_as_DataFrame().join(results[['result', 'functional_obj_id']]).fillna('')
        self._table_axes += ['Результат', 'id Функционального объекта']
        self._table_model.appendColumn(list(map(lambda text: QtGui.QStandardItem(text), df['result'])))
        self._table_model.appendColumn(list(map(lambda text: QtGui.QStandardItem(str(int(text)) if isinstance(text, (int, float)) else ''), df['functional_obj_id'])))
        self._table_model.setHorizontalHeaderLabels(self._table_axes)
        self._table.resizeColumnToContents(len(self._table_axes) - 2) # type: ignore
        self._table.resizeColumnToContents(len(self._table_axes) - 1) # type: ignore
        for row in range(self._table_model.rowCount()):
            self._table_model.setData(self._table_model.index(row, len(self._table_axes) - 2), MainWindow.colorTable.sky_blue, QtCore.Qt.BackgroundRole)
            self._table_model.setData(self._table_model.index(row, len(self._table_axes) - 1), MainWindow.colorTable.sky_blue, QtCore.Qt.BackgroundRole)
        self._save_results_btn.setVisible(True)
        self._table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers) # type: ignore

    def on_save_results(self) -> None:
        fileDialog = QtWidgets.QFileDialog(self)
        fileDialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        fileDialog.setNameFilters(('Modern Excel files (*.xlsx)', 'Excel files (*.xls)', 'OpedDocumentTable files (*.ods)', 'CSV files (*.csv)'))
        filename = self.windowTitle()[self.windowTitle().index('"') + 1:self.windowTitle().rindex('"')]
        t = time.localtime()
        logfile = f'{t.tm_year}-{t.tm_mon:02}-{t.tm_mday:02} ' \
                f'{t.tm_hour:02}-{t.tm_min:02}-{t.tm_sec:02}-{filename[:filename.rindex(".")]}'
        fileDialog.selectNameFilter('CSV files (*.csv)')
        fileDialog.selectFile(logfile)
        if fileDialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        filename = fileDialog.selectedFiles()[0]
        format = fileDialog.selectedNameFilter()[fileDialog.selectedNameFilter().rfind('.'):-1]
        if not filename.endswith(format):
            filename += format
        df = self.table_as_DataFrame()
        save_func = pd.DataFrame.to_csv if filename[filename.rfind('.') + 1:] == 'csv' else pd.DataFrame.to_excel
        save_func(df, filename, index=False)

    def on_prefix_add(self) -> None:
        self._document_address_prefixes.append(ColorizingLine(self.on_prefix_check))
        self._prefixes_group.insertWidget(self._prefixes_group.count() - 4, self._document_address_prefixes[-1])
        if len(self._document_address_prefixes) == 2:
            self._address_prefix_remove_btn.setEnabled(True)
        self.on_prefix_check()

    def on_prefix_remove(self) -> None:
        self._document_address_prefixes.pop()
        widget = self._prefixes_group.itemAt(self._prefixes_group.count() - 5).widget()
        widget.setVisible(False)
        self._prefixes_group.removeWidget(widget)
        if len(self._document_address_prefixes) == 1:
            self._address_prefix_remove_btn.setEnabled(False)
        self.on_prefix_check()

    def on_prefix_check(self, _: Optional[Any] = None, __: Optional[Any] = None) -> None:
        res = 0
        if self._document_fields.address.currentIndex() != 0:
            col = self._document_fields.address.currentIndex()
            for row in range(self._table_model.rowCount()):
                found = False
                for prefix in self._document_address_prefixes:
                    if str(self._table_model.index(row, col).data()).startswith(prefix.text()):
                        self._table_model.setData(self._table_model.index(row, col), MainWindow.colorTable.dark_green, QtCore.Qt.BackgroundRole)
                        found = True
                        break
                if found:
                    res += 1
                else:
                    self._table_model.setData(self._table_model.index(row, col), MainWindow.colorTable.dark_red, QtCore.Qt.BackgroundRole)
        if self._table is not None:
            self._prefixes_group_box.setTitle(f'Префиксы адреса ({res} / {self._table_model.rowCount()}))') # )) = ) , magic

    def on_connection_check(self) -> None:
        host, port_str = (self._database_fields.address.text().split(':') + [str(MainWindow.default_values.db_port)])[0:2]
        try:
            port = int(port_str)
        except ValueError:
            self._db_check_res.setText('<b style=color:red;>x</b>')
            return
        if not (self._db_properties.db_addr == host and self._db_properties.db_port == port and 
                self._db_properties.db_name == self._database_fields.name.text() and
                self._db_properties.db_user == self._database_fields.user.text() and
                self._db_properties.db_pass == self._database_fields.password.text()):
            self._db_properties.close()
            self._db_properties = Properties(host, port, self._database_fields.name.text(),
                    self._database_fields.user.text(), self._database_fields.password.text())
        try:
            with self._db_properties.conn.cursor() as cur:
                cur.execute('SELECT 1')
                assert cur.fetchone()[0] == 1, 'cannot connect to the database'
                cur.execute('SELECT name FROM city_functions ORDER BY 1')
                items = list(itertools.chain.from_iterable(cur.fetchall()))
                self._options_fields.city_function.clear()
                self._options_fields.city_function.addItem('(не выбрано)')
                self._options_fields.city_function.addItems(items)
                self._options_fields.city_function.view().setMinimumWidth(len(max(items, key=len)) * 8)

                cur.execute('SELECT st.name, st.code, st.capacity_min, st.capacity_max, st.status_min,'
                        '       st.status_max, st.is_building, cf.name FROM city_service_types st'
                        '   JOIN city_functions cf on st.city_function_id = cf.id'
                        ' ORDER BY 1')
                self._service_type_params = dict(map(lambda x: (x[0], tuple(x[1:])), cur.fetchall())) # type: ignore
                self._options_fields.service_type_choose.clear()
                self._options_fields.service_type_choose.setEnabled(True)
                self._options_fields.service_type_choose.addItem('(не выбрано)')
                self._options_fields.service_type_choose.addItems(sorted(self._service_type_params.keys()))
                self._options_fields.service_type_choose.view().setMinimumWidth(len(max(self._service_type_params.keys(), key=len)) * 8)
            self._options_fields.city_function.setEnabled(True)
        except Exception:
            self._db_properties.close()
            self._options_fields.service_type_choose.setEnabled(False)
            self._options_fields.city_function.setEnabled(False)
            if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier:
                QtWidgets.QMessageBox.critical(self, 'Ошибка при попытке подключиться к БД', traceback.format_exc())
            self._db_check_res.setText('<b style=color:red;>x</b>')
        else:        
            self._db_check_res.setText('<b style=color:green;>v</b>')

    def on_options_change(self, what_changed: Optional[Union[QtWidgets.QLineEdit, QtWidgets.QComboBox]] = None, _: Optional[str] = None):
        allowed_chars = set((chr(i) for i in range(ord('a'), ord('z') + 1))) | {'_'}
        self._is_options_ok = True

        if what_changed is self._options_fields.service_type_choose:
            old_is_building = self._options_fields.is_building.isChecked()
            if self._options_fields.service_type_choose.currentText() in self._service_type_params:
                service = self._service_type_params[self._options_fields.service_type_choose.currentText()]
                self._options_fields.service_code.setText(service[0])
                self._options_fields.min_capacity.setText(str(service[1]))
                self._options_fields.max_capacity.setText(str(service[2]))
                self._options_fields.min_status.setText(str(service[3]))
                self._options_fields.max_status.setText(str(service[4]))
                self._options_fields.is_building.setChecked(service[5])
                self._options_fields.city_function.setCurrentText(service[6])
                what_changed.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
            else:
                self._options_fields.service_code.setText('')
                self._options_fields.min_capacity.setText('')
                self._options_fields.max_capacity.setText('')
                self._options_fields.min_status.setText('')
                self._options_fields.max_status.setText('')
                self._options_fields.is_building.setChecked(False)
                self._options_fields.city_function.setCurrentIndex(0)
                what_changed.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            if old_is_building != self._options_fields.is_building.isChecked():
                self.on_document_change(self._document_fields.address)
            

        if what_changed is self._options_fields.service_type_choosable and self._options_fields.service_type_choosable.isChecked():
            if self._options_fields.service_type_choose.currentIndex() == 0:
                self._is_options_ok = False
                self._options_fields.service_type_choose.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            else:
                self._options_fields.service_type_choose.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
            self.on_options_change(self._options_fields.service_type_choose)
            return

        if self._options_fields.service_type.text() != '' and '"' not in self._options_fields.service_type.text() \
                and "'" not in self._options_fields.service_type.text():
            self._options_fields.service_type.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
        else:
            self._options_fields.service_type.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            if self._options_fields.service_type.isVisible():
                self._is_options_ok = False
        if self._options_fields.service_code.text() != '' and len(set(self._options_fields.service_code.text()) - allowed_chars - {'-'}) == 0:
            self._options_fields.service_code.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
        else:
            self._is_options_ok = False
            self._options_fields.service_code.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))

        if self._options_fields.city_function.currentIndex() == 0:
            self._is_options_ok = False
            self._options_fields.city_function.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
        else:
            self._options_fields.city_function.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
        
        num_lines_ok = 0
        for line in (self._options_fields.min_capacity, self._options_fields.max_capacity):
            if line.text() != '' and line.text().isdigit():
                line.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
                num_lines_ok += 1
            else:
                self._is_options_ok = False
                line.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
        if num_lines_ok < 2:
            self._is_options_ok = False
        elif num_lines_ok == 2 and int(self._options_fields.max_capacity.text()) < int(self._options_fields.min_capacity.text()):
            self._options_fields.min_capacity.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            self._options_fields.max_capacity.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            self._is_options_ok = False

        num_lines_ok = 0
        for line in (self._options_fields.min_status, self._options_fields.max_status):
            if line.text() != '' and line.text().isdigit():
                line.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_green.getRgb()[:3]))
                num_lines_ok += 1
            else:
                self._is_options_ok = False
                line.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))

        if num_lines_ok < 2:
            self._is_options_ok = False
        elif num_lines_ok == 2 and int(self._options_fields.max_status.text()) < int(self._options_fields.min_status.text()):
            self._options_fields.min_status.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            self._options_fields.max_status.setStyleSheet('background-color: rgb({}, {}, {})'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
            self._is_options_ok = False

        if self._is_options_ok and self._is_document_ok:
            self._load_objects_btn.setEnabled(True)
        else:
            self._load_objects_btn.setEnabled(False)

    def on_choose_change(self, what_changed: QtWidgets.QCheckBox) -> None:
        if what_changed.isChecked():
            self._options_group.replaceWidget(self._options_fields.service_type, self._options_fields.service_type_choose)
            self._options_fields.service_type.setVisible(False)
            self._options_fields.service_type_choose.setVisible(True)
            self._options_fields.service_code.setEnabled(False)
            self._options_fields.min_capacity.setEnabled(False)
            self._options_fields.max_capacity.setEnabled(False)
            self._options_fields.min_status.setEnabled(False)
            self._options_fields.max_status.setEnabled(False)
            self._options_fields.city_function.setEnabled(False)
            self._options_fields.is_building.setEnabled(False)
            self.on_options_change(self._options_fields.service_type_choose)
        else:
            self._options_group.replaceWidget(self._options_fields.service_type_choose, self._options_fields.service_type)
            self._options_fields.service_type_choose.setVisible(False)
            self._options_fields.service_type.setVisible(True)
            self._options_fields.service_code.setEnabled(True)
            self._options_fields.min_capacity.setEnabled(True)
            self._options_fields.max_capacity.setEnabled(True)
            self._options_fields.min_status.setEnabled(True)
            self._options_fields.max_status.setEnabled(True)
            if self._db_properties.connected:
                self._options_fields.city_function.setEnabled(True)
            self._options_fields.is_building.setEnabled(True)
        self.on_options_change()

    def on_document_change(self, what_changed: Optional[QtWidgets.QComboBox] = None,
            previous_value: Optional[int] = None) -> None:
        self._is_document_ok = True
        if self._table is None:
            return
        if what_changed is not None:
            if what_changed is self._document_fields.address and what_changed.currentIndex() != 0:
                self.on_prefix_check()
            elif what_changed.currentIndex() == 0:
                if what_changed is self._document_fields.latitude or what_changed is self._document_fields.longitude or \
                        what_changed is self._document_fields.address and self._options_fields.is_building.isChecked():
                    what_changed.setStyleSheet('background-color: rgb({}, {}, {});'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
                    self._is_document_ok = False
                else:
                    what_changed.setStyleSheet('background-color: rgb({}, {}, {});'.format(*MainWindow.colorTable.grey.getRgb()[:3]))
            else:
                what_changed.setStyleSheet('')
                col = what_changed.currentIndex()
                for row in range(self._table_model.rowCount()):
                    self._table_model.setData(self._table_model.index(row, col), MainWindow.colorTable.light_green, QtCore.Qt.BackgroundRole)

            if previous_value is not None and previous_value != 0:
                is_used = False
                field: QtWidgets.QComboBox
                for field in self._document_fields:
                    if field.currentIndex() == previous_value:
                        is_used = True
                if not is_used:
                    col = previous_value
                    for row in range(self._table_model.rowCount()):
                        self._table_model.setData(self._table_model.index(row, col), QtGui.QColor(QtCore.Qt.white), QtCore.Qt.BackgroundRole)
                        
        for field in self._document_fields:
            if field.currentIndex() == 0:
                if not (field is self._document_fields.address and self._options_fields.is_building.isChecked() or \
                        field is self._document_fields.latitude or field is self._document_fields.longitude):
                    field.setStyleSheet('background-color: rgb({}, {}, {});'.format(*MainWindow.colorTable.grey.getRgb()[:3]))
                else:
                    field.setStyleSheet('background-color: rgb({}, {}, {});'.format(*MainWindow.colorTable.light_red.getRgb()[:3]))
                    self._is_document_ok = False
            elif field is not self._document_fields.address:
                field.setStyleSheet('')
                col = field.currentIndex()
                color = MainWindow.colorTable.light_green
                for field_inner in self._document_fields:
                    if field_inner is not field and field_inner.currentIndex() == col:
                        color = MainWindow.colorTable.grey
                for row in range(self._table_model.rowCount()):
                    self._table_model.setData(self._table_model.index(row, col), color, QtCore.Qt.BackgroundRole)
            else:
                field.setStyleSheet('background-color: rgb({}, {}, {});'.format(*MainWindow.colorTable.grey.getRgb()[:3]))

        if self._is_options_ok and self._is_document_ok:
            self._load_objects_btn.setEnabled(True)
        else:
            self._load_objects_btn.setEnabled(False)

if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    app.setWindowIcon(QtGui.QIcon(QtGui.QPixmap('icon.png')))
    app.setApplicationName('Добавление сервисов')

    window = MainWindow()
    window.show()

    exit(app.exec())