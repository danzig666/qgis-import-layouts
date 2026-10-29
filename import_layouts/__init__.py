def classFactory(iface):
    from .plugin import ImportLayoutsPlugin
    return ImportLayoutsPlugin(iface)
