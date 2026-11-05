def _hard_cut(s: str, limit: int) -> list[str]:
    return [s[i : i + limit] for i in range(0, len(s), limit)]


def _pack(pieces: list[str], sep: str, limit: int) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        cand = f"{cur}{sep}{p}" if cur else p
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def split_reply(text: str, limit: int = 1500) -> list[str]:
    if not text.strip():
        return ["(no reply)"]
    out: list[str] = []
    for chunk in _pack(text.strip().split("\n\n"), "\n\n", limit):
        if len(chunk) <= limit:
            out.append(chunk)
            continue
        for line_chunk in _pack(chunk.split("\n"), "\n", limit):
            if len(line_chunk) <= limit:
                out.append(line_chunk)
            else:
                out.extend(_hard_cut(line_chunk, limit))
    return [c.strip() for c in out if c.strip()]
