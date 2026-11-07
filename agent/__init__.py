"""The studio: an ADK 2 workflow that writes a video and only ships what frameproof accepts.

    intake -> (reader_1, reader_2, reader_3) -> panel -> proposer -> gate -> router
                                                           ^                  |
                                                           +---- REVISE ------+
                                                 ACCEPT -> desk (pause) -> deliver
                                                 GIVE_UP -> give_up

The gate is frameproof, deterministic code: the proposer never sees hidden checks,
and the gate never sees the proposer's prompt.
"""
