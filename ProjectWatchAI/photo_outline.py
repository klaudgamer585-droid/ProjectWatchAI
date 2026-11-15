from base64 import b64encode
from io import BytesIO

import streamlit as st


def outline_photo(photo, key, initial):
    data = BytesIO()
    photo.save(data, format="JPEG", quality=85)
    widget = st.components.v2.component(
        "yolo_outline",
        html='<canvas aria-label="Outline the feature in the photo"></canvas>'
             '<div><button type="button" id="undo">Undo point</button> '
             '<button type="button" id="clear">Clear outline</button> '
             '<button type="button" id="apply">Use outline</button></div>',
        css="canvas {display:block;max-width:100%;height:auto;cursor:crosshair;margin-bottom:8px}",
        js="""
        export default function({parentElement, data, setStateValue}) {
            const canvas = parentElement.querySelector('canvas');
            const image = new Image();
            const points = data.points.map(point => [...point]);
            const context = canvas.getContext('2d');
            function draw() {
                if (!image.complete || !image.naturalWidth) return;
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                context.drawImage(image, 0, 0);
                if (!points.length) return;
                context.beginPath();
                for (const [x, y] of points) context.lineTo(x * canvas.width, y * canvas.height);
                context.closePath();
                context.strokeStyle = '#00e5ff';
                context.lineWidth = 3;
                context.stroke();
                for (const [x, y] of points) {
                    context.beginPath();
                    context.arc(x * canvas.width, y * canvas.height, 4, 0, Math.PI * 2);
                    context.fillStyle = '#00e5ff';
                    context.fill();
                }
            }
            image.onload = draw;
            image.src = data.image;
            canvas.onclick = event => {
                if (points.length >= 250) return;
                const rect = canvas.getBoundingClientRect();
                points.push([(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height]);
                draw();
            };
            parentElement.querySelector('#undo').onclick = () => { points.pop(); draw(); };
            parentElement.querySelector('#clear').onclick = () => { points.length = 0; draw(); };
            parentElement.querySelector('#apply').onclick = () => setStateValue('points', points.map(point => [...point]));
        }
        """,
    )
    return widget(
        key=key,
        data={"image": "data:image/jpeg;base64," + b64encode(data.getvalue()).decode(), "points": initial},
        default={"points": initial},
        on_points_change=lambda: None,
    ).points
