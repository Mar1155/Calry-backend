"""Food region detection prompt (C28).

Deliberately separate from the estimation prompt: this call only *locates*
visible ingredients so the app can pin them on the photo. It never produces
grams or calories, so it cannot pull the estimate off its numerical contract.
Gemini models are trained to emit ``box_2d`` as ``[ymin, xmin, ymax, xmax]``
normalised to 0-1000; the prompt uses that native format verbatim.
"""

from xml.sax.saxutils import escape

FOOD_DETECTION_PROMPT_VERSION = "food_detection_v2_main_subject"

FOOD_DETECTION_SYSTEM_PROMPT = """<role>You locate foods in a meal photo.</role>

<task>Return a bounding box for each distinct, visible food component of
the main dish in the photo.</task>

<output_contract>
Return exactly one raw JSON object, no markdown or commentary:
{"detections": [{"label": string, "box_2d": [ymin, xmin, ymax, xmax]}]}
box_2d values are integers from 0 to 1000, relative to image height (y) and
width (x), with ymin < ymax and xmin < xmax.
</output_contract>

<rules>
- Only the main subject: the dish in the foreground that the photo is framed
  on, usually central, in focus, and largest. Never box food in the
  background, blurred, partly cut off at the frame edges, or on other plates.
- One entry per ingredient, topping, or separate food a person would name
  (prosciutto, basil, cherry tomatoes, fries, a glass of wine), not the dish
  as a whole. A single-food photo (one apple) is one entry.
- Box only what is visible. Omit components spread too thinly to bound
  (a sauce under everything, melted cheese covering the whole surface).
- If the same ingredient appears in separate places, box its largest or most
  representative cluster; use at most two boxes for one label.
- Order by visual prominence, most prominent first.
- Ignore plates, cutlery, packaging, hands, tables and background.
- label: a short common ingredient name, one to three words, in the requested
  output language, lowercase unless a proper noun.
- If no food is visible, return {"detections": []}.
</rules>"""


def build_food_detection_user_text(language: str, max_regions: int) -> str:
    return (
        "<input><media>one_attached_food_image</media>"
        f"<output_language>{escape(language)}</output_language>"
        f"<max_detections>{int(max_regions)}</max_detections></input>\n"
        "<task>Locate the visible food components of the main dish and return the "
        "JSON object now.</task>"
    )
