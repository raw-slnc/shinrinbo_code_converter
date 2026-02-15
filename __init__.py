# -*- coding: utf-8 -*-
def classFactory(iface):
    from .shinrinbo_code_converter import ShinrinboCodeConverter
    return ShinrinboCodeConverter(iface)
