import os

from qgis.PyQt import sip
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&Import Layouts from Project"
TITLE = "Import Layouts from Project…"


class ImportLayoutsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = None
        self.designer_actions = {}  # designer interface -> QAction in its Layout menu
        self.dialog = None

    def initGui(self):
        self.action = QAction(self.icon, TITLE, self.iface.mainWindow())
        self.action.setToolTip("Import print layouts from another .qgs/.qgz project")
        self.action.triggered.connect(lambda: self.run())
        self.iface.addPluginToMenu(MENU, self.action)

        # Also offer the action in the "Layout" menu of every layout designer window.
        self.iface.layoutDesignerOpened.connect(self.add_designer_action)
        self.iface.layoutDesignerWillBeClosed.connect(self.remove_designer_action)
        for designer in self.iface.openLayoutDesigners():
            self.add_designer_action(designer)

    def unload(self):
        self.iface.layoutDesignerOpened.disconnect(self.add_designer_action)
        self.iface.layoutDesignerWillBeClosed.disconnect(self.remove_designer_action)
        for designer in list(self.designer_actions):
            self.remove_designer_action(designer)
        if self.action:
            self.iface.removePluginMenu(MENU, self.action)
            self.action.deleteLater()
            self.action = None
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            self.dialog.deleteLater()
            self.dialog = None

    def add_designer_action(self, designer):
        menu = designer.layoutMenu()
        if menu is None or designer in self.designer_actions:
            return
        action = QAction(self.icon, TITLE, menu)
        action.triggered.connect(lambda: self.run(designer.window()))
        # Place it just before the separator above "Close" / at the end of the menu.
        actions = menu.actions()
        before = next((a for a in reversed(actions) if a.isSeparator()), None)
        if before is not None:
            menu.insertAction(before, action)
        else:
            menu.addAction(action)
        self.designer_actions[designer] = action

    def remove_designer_action(self, designer):
        action = self.designer_actions.pop(designer, None)
        if action is None:
            return
        try:
            designer.layoutMenu().removeAction(action)
        except RuntimeError:  # designer already destroyed
            pass
        action.deleteLater()

    def run(self, parent=None):
        from .dialog import ImportLayoutsDialog
        parent = parent or self.iface.mainWindow()
        if self.dialog is not None and sip.isdeleted(self.dialog):
            self.dialog = None  # its designer window was closed
        if self.dialog is None or self.dialog.parent() is not parent:
            if self.dialog is not None:
                self.dialog.deleteLater()
            self.dialog = ImportLayoutsDialog(self.iface, parent)
        self.dialog.refresh_destination()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
