"""The RDK class of primitive: FLUOVIEW's Remote Development Kit, an XML-RPC server.

Everything the driver asks FLUOVIEW, and everything it tells it, goes through
this one way in: ``client.py`` opens the connection, ``transfer.py`` carries a
request and its reply for the write loops, ``reads.py`` holds the readings,
``writes.py`` the changes as plain data, ``image_data.py`` reads the pixels of
a saved picture back, and ``profile.py`` is what the dispatcher is handed.
"""
