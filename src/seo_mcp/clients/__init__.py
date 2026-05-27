"""Network client layer. Each module wraps one upstream API behind a small
class. Tools call clients; tests replace clients with fakes. No tool imports a
networking library directly.
"""
