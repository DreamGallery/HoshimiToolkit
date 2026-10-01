import UnityPy
from PIL import Image
from pathlib import Path
import proto.octodb_pb2 as octop
import src.rich_console as console


def unpack_to_image(asset_bytes: bytes, dest_path: str):
    env = UnityPy.load(asset_bytes)
    for obj in env.objects:
        if obj.type.name == "Texture2D":
            data = obj.read()
            name = getattr(data, "m_Name", getattr(data, "name", ""))
            if name.startswith("env") or name.startswith("img"):
                try:
                    store_path = f"{Path(dest_path).parent}/image/Texture2D"
                    Path(store_path).mkdir(parents=True, exist_ok=True)
                    img = data.image
                    img.save(f"{store_path}/{name}.png")
                except Exception as exc:
                    console.error(f"Failed to convert '{name}' to image: {exc}")


def resize_image(inputs: str, output: str, size: tuple = (0, 0)):
    try:
        original_image = Image.open(inputs)
    except FileNotFoundError:
        console.error(f"No such file or directory: '{inputs}'")
        return
    resized_image = original_image.resize(size)
    resized_image.save(output)
    console.succeed(f"Img '{inputs}' has been successfully scaled")


def image_scale(database: octop.Database, source: str, dest: str):
    Path(dest).mkdir(parents=True, exist_ok=True)
    for asset in database.assetBundleList:
        name = asset.name
        if name.startswith("img_card_full_1_") or (
            name.startswith("img_ui_hero_") and not name.startswith("img_ui_hero_event")
        ):
            size = (2560, 1440)
        else:
            continue
        file = f"{source}/Texture2D/{name}.png"
        if Path(file).exists():
            resize_image(inputs=file, output=f"{dest}/{name}.png", size=size)
