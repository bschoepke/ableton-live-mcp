from __future__ import absolute_import, print_function

from .bridge import AbletonObjectMCP


def create_instance(c_instance):
    return AbletonObjectMCP(c_instance)
