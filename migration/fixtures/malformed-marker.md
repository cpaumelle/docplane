# Example With A Malformed Marker

This synthetic fixture reproduces, generically, the historical brace-imbalance
defect. The line below is a MALFORMED redaction marker: it has an unbalanced
trailing brace and an extra leading brace. The transform must REJECT this input
(never carry it forward, never produce it).

Broken marker: {<REDACTED:PASSWORD:example-0001>}}

The correct, well-formed form would be: <REDACTED:PASSWORD:example-0001>
