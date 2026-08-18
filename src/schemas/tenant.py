from enum import Enum


class ShopType(str, Enum):
    ICE_CREAM = "ice_cream"

SHOP_TYPE_SLUGS = {
    ShopType.ICE_CREAM: "icecream-v1",
}