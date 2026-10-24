export function splitStableMarkdown(text) {
  const source = String(text || "");
  if (!source) return { stable: "", tail: "" };

  const fenceRe = /^ {0,3}(`{3,}|~{3,})/;
  const listRe = /^ {0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$))/;
  const headingRe = /^ {0,3}#{1,6}(?:[ \t]|$)/;
  const hrRe = /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;

  let stableEnd = 0;
  let blockCandidateEnd = 0;
  let inCode = false;
  let inList = false;
  let inRawHtml = false;
  let listAfterBlank = false;
  let htmlAfterBlank = false;
  let fenceChar = "";
  let fenceLength = 0;

  let lineStart = 0;
  while (lineStart < source.length) {
    const newlineIndex = source.indexOf("\n", lineStart);
    const lineEnd = newlineIndex === -1 ? source.length : newlineIndex + 1;
    const line = source.slice(lineStart, lineEnd);
    const lineBody = line.replace(/\r?\n$/, "");
    const complete = newlineIndex !== -1;

    if (inCode) {
      const match = line.match(fenceRe);
      const closingFence =
        match &&
        match[1][0] === fenceChar &&
        match[1].length >= fenceLength &&
        line.slice(match[0].length).trim() === "";
      if (closingFence && complete) {
        inCode = false;
        stableEnd = lineEnd;
      }
    } else if (inList) {
      if (!line.trim()) {
        listAfterBlank = true;
        blockCandidateEnd = lineEnd;
      } else if (listAfterBlank) {
        const continuesList = listRe.test(lineBody) || /^[ \t]/.test(line);
        if (!continuesList) {
          inList = false;
          listAfterBlank = false;
          stableEnd = blockCandidateEnd;
        }
      }
    } else if (inRawHtml) {
      if (!line.trim()) {
        htmlAfterBlank = true;
        blockCandidateEnd = lineEnd;
      } else if (htmlAfterBlank && !/^ {0,3}</.test(line)) {
        inRawHtml = false;
        htmlAfterBlank = false;
        stableEnd = blockCandidateEnd;
      }
    } else if (!line.trim()) {
      stableEnd = lineEnd;
    } else if (complete && (headingRe.test(line) || hrRe.test(lineBody))) {
      stableEnd = lineEnd;
    } else {
      const fence = line.match(fenceRe);
      if (fence) {
        inCode = true;
        fenceChar = fence[1][0];
        fenceLength = fence[1].length;
      } else if (listRe.test(lineBody)) {
        inList = true;
        listAfterBlank = false;
      } else if (/^ {0,3}</.test(line)) {
        inRawHtml = true;
        htmlAfterBlank = false;
      }
    }

    lineStart = lineEnd;
  }

  return {
    stable: source.slice(0, stableEnd),
    tail: source.slice(stableEnd),
  };
}
