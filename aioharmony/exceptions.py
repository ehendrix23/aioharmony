"""
Define the exception classes for aioharmony module.
"""


class HarmonyException(Exception):
    """Top level Harmony Exception"""


class HarmonyClient(HarmonyException):
    """
    Top level exception for HarmonyClient
    """


class TimeOut(HarmonyClient, TimeoutError):
    """
    Raised on timeouts
    """
