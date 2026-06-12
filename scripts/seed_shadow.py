"""Dev-only: run a batch of paraphrase questions through shadow_match to populate
dash_query_bank_shadow with a real similarity distribution, for Q4 threshold
tuning. Does NOT serve or run the agent — pure embed + NN + log. Run inside the
container: docker exec cp-api python /app/scripts/seed_shadow.py
"""
import asyncio
from dash.learning.query_capture import shadow_match

SLUG = "citypharma"

# Paraphrases spanning close → far of the seeded base questions, so the logged
# sims cover the 0.80–0.99 band where the threshold decision lives.
PARAPHRASES = [
    # category count
    "how many categories do we carry", "number of distinct product categories",
    "count of product categories", "how many different categories are there",
    # generic names
    "how many distinct generics are registered", "count of unique generic names",
    "number of generic molecules in the catalog",
    # article count
    "total number of articles in the catalog", "how many products do we have",
    "count of all articles", "how many SKUs in total",
    # top category
    "which category has the most products", "biggest category by article count",
    "what category holds the most items",
    # sites with stock
    "how many branches hold stock", "across how many sites do we have stock",
    "number of sites we stock products in",
    # total stock qty
    "overall stock units we hold in total", "sum of stock across every branch",
    "total quantity of stock available", "how many units of stock do we have total",
    # myanmar labeling
    "how many products have myanmar labels", "count of items with mm labeling",
    # compositions
    "how many unique compositions exist", "number of distinct compositions",
    # cost value
    "total inventory cost value", "what is the weighted cost of all inventory",
    "total value of stock on hand",
    # out of stock
    "how many articles are out of stock", "count of products with zero stock",
    # top 5 categories
    "top five categories by product count", "five largest categories by SKUs",
    # distinct article codes in stock
    "how many distinct article codes are in stock", "count of unique stock article codes",
    # far / unrelated-ish (should land low sim)
    "what is paracetamol used for", "is amoxicillin in stock at my branch",
    "substitutes for ibuprofen", "what do you have for fever",
    "tell me about the company", "how many staff work here",
]


async def main():
    n = 0
    for q in PARAPHRASES:
        try:
            await shadow_match(SLUG, q)
            n += 1
        except Exception as e:
            print("err", q[:30], e)
    print(f"shadow_match ran for {n}/{len(PARAPHRASES)} paraphrases")


asyncio.run(main())
