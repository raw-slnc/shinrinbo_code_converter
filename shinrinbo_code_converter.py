# -*- coding: utf-8 -*-
import os.path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction


class ShinrinboCodeConverter:
    """QGIS Plugin: 森林簿コード変換"""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = self.tr('&森林簿コード変換')
        self.dialog = None

    def tr(self, message):
        return QCoreApplication.translate('ShinrinboCodeConverter', message)

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, 'icon.png'))
        action = QAction(icon, self.tr('森林簿コード変換'), self.iface.mainWindow())
        action.triggered.connect(self.run)
        self.iface.addVectorToolBarIcon(action)
        self.iface.addPluginToVectorMenu(self.menu, action)
        self.actions.append(action)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removeVectorToolBarIcon(action)

    def run(self):
        from .shinrinbo_dialog import ShinrinboDialog
        if self.dialog is None:
            self.dialog = ShinrinboDialog(self.iface, self.iface.mainWindow())
            self.dialog.destroyed.connect(self._on_dialog_destroyed)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _on_dialog_destroyed(self):
        self.dialog = None
