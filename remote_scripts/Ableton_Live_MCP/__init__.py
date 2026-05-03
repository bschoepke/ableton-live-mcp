from __future__ import absolute_import, print_function

from .bridge import AbletonLiveMCP


def create_instance(c_instance):
    return AbletonLiveMCP(c_instance)
