type Box = { x: number; y: number; w: number; h: number };

/** On-screen size of the face zoom: 240px, or 70% of a narrow screen. */
const zoomPx = () => Math.min(240, Math.round(window.innerWidth * 0.7));

/** The face and its surroundings, cropped from the photo: `context` face-sizes across, square, with
 *  the face's box outlined. Box coordinates are fractions of the (rotated) photo. */
export function FaceZoom({ src, face, aspect, context, size }: {
  src: string;
  face: Box;
  aspect: number;
  context: number;
  /** On-screen width and height in px (default: 240, or 70% of a narrow screen). */
  size?: number;
}) {
  // Work in units of the photo's height so the crop is square on screen.
  const W = aspect;
  const side = Math.min(Math.max(face.w * W, face.h) * context, W, 1);
  const rw = side / W;
  const rh = side;
  const rx = Math.min(Math.max(face.x + face.w / 2 - rw / 2, 0), 1 - rw);
  const ry = Math.min(Math.max(face.y + face.h / 2 - rh / 2, 0), 1 - rh);
  const px = size ?? zoomPx();
  const scale = px / side; // screen px per photo-height unit
  return (
    <div className="facezoom__view" style={{ width: px, height: px }}>
      <img
        src={src}
        alt=""
        draggable={false}
        style={{ width: W * scale, height: scale, left: -rx * W * scale, top: -ry * scale }}
      />
      <span
        className="facezoom__box"
        style={{ left: (face.x - rx) * W * scale, top: (face.y - ry) * scale, width: face.w * W * scale, height: face.h * scale }}
      />
    </div>
  );
}
