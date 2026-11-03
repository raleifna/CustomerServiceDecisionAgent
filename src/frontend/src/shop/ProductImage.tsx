import {
  Bean,
  BedDouble,
  BookOpen,
  Coffee,
  CookingPot,
  CupSoda,
  Fan,
  Flower2,
  Footprints,
  Gamepad2,
  Gem,
  Headphones,
  Keyboard,
  Leaf,
  LucideIcon,
  Package,
  Shirt,
  Smartphone,
  Sofa,
  Speaker,
  Watch,
  Wind,
} from "lucide-react";

import { ProductCard } from "./api";

const icons: Record<string, LucideIcon> = {
  bean: Bean,
  "bed-double": BedDouble,
  "book-open": BookOpen,
  coffee: Coffee,
  "cooking-pot": CookingPot,
  "cup-soda": CupSoda,
  fan: Fan,
  "flower-2": Flower2,
  footprints: Footprints,
  "gamepad-2": Gamepad2,
  gem: Gem,
  headphones: Headphones,
  keyboard: Keyboard,
  leaf: Leaf,
  package: Package,
  shirt: Shirt,
  smartphone: Smartphone,
  sofa: Sofa,
  speaker: Speaker,
  watch: Watch,
  wind: Wind,
};

type Props = {
  product: Pick<ProductCard, "icon" | "palette" | "title"> | null | undefined;
  size?: "xs" | "sm" | "md" | "lg";
  // Gallery thumbnails: the same product drawn from a few "angles".
  variant?: 0 | 1 | 2 | 3;
  className?: string;
};

// Products have no photography in the mock dataset, so each one gets a
// generated studio-style illustration: category palette + product glyph.
export function ProductImage({ product, size = "md", variant = 0, className = "" }: Props) {
  const Icon = icons[product?.icon ?? "package"] ?? Package;
  const palette = product?.palette ?? "slate";
  return (
    <div
      className={`product-image product-image--${size} product-image--${palette} product-image--v${variant} ${className}`}
      role="img"
      aria-label={product?.title ?? "商品图片"}
    >
      <span className="product-image__halo" />
      <span className="product-image__floor" />
      <Icon className="product-image__glyph" strokeWidth={1.35} />
    </div>
  );
}

export function Stars({ value }: { value: number }) {
  const rounded = Math.round(value);
  return (
    <span className="stars" aria-label={`${value} 星`}>
      {[1, 2, 3, 4, 5].map((index) => <i key={index} className={index <= rounded ? "on" : ""}>★</i>)}
    </span>
  );
}
