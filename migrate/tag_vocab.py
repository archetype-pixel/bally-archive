"""Zero-shot tag vocabulary for the archive. Each entry: (group, tag, prompt shown to CLIP).
Groups are scored independently; the top tag per group per photo is the candidate label."""

def _p(items, template):
    return [(grp, tag, template.format(x)) for grp, tag, x in items]

VOCAB = []

# activity / sport
VOCAB += _p([("activity", t, x) for t, x in [
    ("golf", "a golf shoe with spikes or a golf scene"), ("tennis", "a tennis shoe or a tennis scene"),
    ("ballet", "a ballet slipper or ballet dancer"), ("skiing", "a ski boot or a skiing scene on snow"),
    ("apres-ski", "an après-ski snow boot with fur or padding"), ("mountaineering", "a heavy mountaineering boot or climbers on a mountain"),
    ("hiking", "a hiking boot or hikers on a trail"), ("football", "a football boot with studs"),
    ("riding", "a riding boot or horse riding"), ("cycling", "a cycling shoe or cyclist"),
    ("sailing", "a boat shoe or sailing scene"), ("running", "a running shoe or athlete running"),
    ("beach", "a beach sandal or swimmers at the beach"), ("hunting", "a hunting boot or hunting scene"),
    ("motoring", "a driving shoe or a vintage car"), ("dancing", "a dancing shoe or people dancing"),
    ("ice-skating", "an ice skate or skating rink"), ("none", "a plain studio photo of an ordinary shoe or bag"),
]], "{}")

# shoe type
VOCAB += _p([("shoe_type", t, x) for t, x in [
    ("oxford", "an oxford lace-up dress shoe"), ("brogue", "a brogue shoe with perforated wingtip"),
    ("derby", "a derby open-laced shoe"), ("loafer", "a slip-on loafer"), ("penny-loafer", "a penny loafer"),
    ("monk-strap", "a monk strap shoe with buckle"), ("pump", "a women's court shoe pump"), ("stiletto", "a stiletto high heel"),
    ("kitten-heel", "a kitten heel shoe"), ("slingback", "a slingback shoe"), ("peep-toe", "a peep toe shoe"),
    ("mary-jane", "a mary jane shoe with strap"), ("ballet-flat", "a ballet flat"), ("sandal", "a sandal"),
    ("mule", "a backless mule"), ("wedge", "a wedge heel shoe"), ("platform", "a platform sole shoe"),
    ("ankle-boot", "an ankle boot"), ("knee-boot", "a knee-high boot"), ("chelsea-boot", "a chelsea boot with elastic sides"),
    ("lace-up-boot", "a lace-up boot"), ("moon-boot", "a padded moon boot"), ("sneaker", "a sneaker trainer"),
    ("high-top-sneaker", "a high-top sneaker"), ("slipper", "a house slipper"), ("espadrille", "an espadrille with rope sole"),
    ("moccasin", "a moccasin"), ("clog", "a wooden clog"), ("boat-shoe", "a boat shoe"), ("desert-boot", "a desert boot"),
    ("children-shoe", "a small child's shoe"), ("shoe-last", "a wooden shoe last"), ("not-a-shoe", "not a shoe"),
]], "{}")

# bag type
VOCAB += _p([("bag_type", t, x) for t, x in [
    ("tote", "a tote bag"), ("shoulder-bag", "a shoulder bag"), ("crossbody", "a small crossbody bag"),
    ("clutch", "a clutch evening bag"), ("backpack", "a backpack"), ("briefcase", "a briefcase"),
    ("suitcase", "a suitcase or travel trunk"), ("duffle", "a duffle holdall bag"), ("wallet", "a wallet or card holder"),
    ("belt", "a leather belt"), ("bucket-bag", "a bucket bag"), ("top-handle", "a structured top handle handbag"),
    ("not-a-bag", "not a bag"),
]], "{}")

# material
VOCAB += _p([("material", t, x) for t, x in [
    ("patent-leather", "glossy patent leather"), ("suede", "suede"), ("smooth-leather", "smooth calf leather"),
    ("crocodile", "crocodile or alligator skin"), ("snakeskin", "snakeskin python"), ("lizard", "lizard skin"),
    ("canvas", "canvas textile"), ("wool", "wool felt or knit"), ("fur", "fur or shearling"), ("nylon", "nylon"),
    ("velvet", "velvet"), ("satin", "satin silk"), ("metallic", "metallic gold or silver leather"), ("raffia", "woven raffia or straw"),
    ("rubber", "rubber"), ("denim", "denim"), ("tweed", "tweed"), ("embroidered", "embroidered fabric"),
    ("woven-leather", "woven braided leather"), ("printed", "printed pattern"), ("paper", "printed paper"),
]], "a photo of a product made of {}")

# media type (what kind of picture is this)
VOCAB += _p([("media", t, x) for t, x in [
    ("product-photo", "a studio product photo on a plain background"), ("poster", "a vintage advertising poster illustration"),
    ("advertisement", "a magazine advertisement page"), ("people-photo", "a photograph of people"),
    ("shop-window", "a shop window or store interior"), ("factory", "a factory or workshop with machines"),
    ("packaging", "a shoe box or product packaging"), ("catalogue-page", "a printed catalogue page"),
    ("drawing", "a sketch or technical drawing"), ("document", "a typed document or letter"),
    ("building", "a building exterior"), ("event", "a fashion show or exhibition event"),
    ("portrait", "a formal portrait of a person"), ("landscape", "a landscape or mountain scene"),
    ("logo", "a brand logo or lettering"), ("film-still", "a film or television still"),
]], "{}")

# occasion
VOCAB += _p([("occasion", t, x) for t, x in [
    ("evening", "an elegant evening or formal wear item"), ("business", "a classic business work item"),
    ("casual", "a casual everyday item"), ("sport", "a sports item"), ("wedding", "a bridal wedding item"),
    ("winter", "a winter cold weather item"), ("summer", "a light summer item"),
]], "{}")

# who it is for
VOCAB += _p([("wearer", t, x) for t, x in [
    ("mens", "a men's shoe or accessory"), ("womens", "a women's shoe or accessory"), ("childrens", "a child's shoe"),
    ("not-applicable", "not a wearable product"),
]], "{}")

# heel / sole construction
VOCAB += _p([("construction", t, x) for t, x in [
    ("flat", "a flat sole"), ("low-heel", "a low heel"), ("high-heel", "a high heel"), ("block-heel", "a chunky block heel"),
    ("lug-sole", "a thick lug rubber sole"), ("crepe-sole", "a crepe sole"), ("leather-sole", "a leather sole"),
    ("buckle", "a buckle fastening"), ("laces", "lace fastening"), ("zip", "a zip fastening"), ("elastic", "elastic gore"),
    ("bow", "a decorative bow"), ("tassel", "a tassel"), ("fringe", "fringe"), ("chain", "a metal chain"),
    ("strap", "an ankle strap"), ("perforated", "perforated leather"), ("two-tone", "two-tone colour blocking"),
]], "a shoe with {}")

# visible branding
VOCAB += _p([("branding", t, x) for t, x in [
    ("bally-stripe", "the red and white Bally stripe"), ("crest-logo", "a heraldic crest emblem"),
    ("b-monogram", "a B monogram pattern"), ("swiss-cross", "a Swiss cross"), ("wordmark", "the brand name printed in capital letters"),
    ("no-visible-logo", "no visible logo"),
]], "a product with {}")

# dominant colour
VOCAB += _p([("colour", t, x) for t, x in [
    ("black", "black"), ("white", "white"), ("brown", "brown"), ("tan", "tan beige"), ("red", "red"), ("burgundy", "burgundy bordeaux"),
    ("navy", "navy blue"), ("blue", "blue"), ("green", "green"), ("yellow", "yellow"), ("orange", "orange"), ("pink", "pink"),
    ("purple", "purple"), ("grey", "grey"), ("gold", "gold metallic"), ("silver", "silver metallic"), ("multicolour", "multicoloured"),
]], "a {} coloured product")
