import { useRef, useState } from "react";
import {
  canNativeShare, copyCard, copyText, deepLink, fileName, nativeShare, savePng,
} from "./share";

interface Props {
  /** builds the export card; called inside the click gesture */
  compose: () => Promise<HTMLCanvasElement>;
  title: () => string;
  link?: () => string;
}

/** The share/copy/png/link row. One instance per shareable thing; the note
 *  replaces the buttons for a beat so feedback lands where the eye already is. */
export default function ShareBar({ compose, title, link }: Props) {
  const noteTimer = useRef<number>(0);
  const [note, setNote] = useState<string | null>(null);

  const flash = (msg: string) => {
    setNote(msg);
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setNote(null), 1800);
  };
  const theLink = () => (link ? link() : deepLink());

  const doPng = () => {
    compose()
      .then((cv) => savePng(cv, fileName(title())))
      .then(() => flash("png saved"), () => flash("export failed"));
  };
  const doCopy = () => {
    copyCard(compose).then((ok) => {
      if (ok) { flash("image copied"); return; }
      // clipboard images are still gated in some browsers; the file is the fallback
      compose()
        .then((cv) => savePng(cv, fileName(title())))
        .then(() => flash("copy blocked · png saved"), () => flash("export failed"));
    });
  };
  const doLink = () => {
    copyText(theLink()).then((ok) => flash(ok ? "link copied" : "copy blocked"));
  };
  const doShare = () => {
    compose()
      .then((cv) => nativeShare(cv, title(), theLink()))
      .then((ok) => { if (!ok) flash("share failed"); }, () => flash("export failed"));
  };

  return (
    <div className="sharebar" role="group" aria-label="share this">
      {note ? (
        <span className="sharenote" role="status">{note}</span>
      ) : (
        <>
          {canNativeShare && <button type="button" onClick={doShare} title="share the card image">share</button>}
          <button type="button" onClick={doCopy} title="copy the card image to the clipboard">copy</button>
          <button type="button" onClick={doPng} title="download the card as a png">png</button>
          <button type="button" onClick={doLink} title="copy a link to this view">link</button>
        </>
      )}
    </div>
  );
}
