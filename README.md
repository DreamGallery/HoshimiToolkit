# HoshimiToolkit/Idoly Pride
A modified fork from [MalitsPlus/HoshimiToolkit](https://github.com/MalitsPlus/HoshimiToolkit) `IDOLY PRIDE`(ipr) components.

## First things first

***As a courtesy to other fans, please refrain from spoiling unreleased contents if any are found after decrypting.***

## How to use

1. Install the requirements at the root folder
    ```
    pip install -r requirements.txt
    ```
2. Move your `octocacheevai` into `cache/` folder. You can get the `octocacheevai` under
   ```
   /data_mirror/data_ce/null/0/game.qualiarts.idolypride/files/octo/pdb/212/205051/
   ```
    from your root enabled device or emulator.  

3. Read the comments and edit the `config.ini` according to your needs.  
   
4. Run `main.py`.
    ```
    python main.py
    ```
    The update data will make a copy in the folder named with the database revision under `cache/update` if you set `UPDATE_FLAG` to `True` in `config.ini`, else it will only merge to default directory.

※ Notice: 
This is a simple migration of `HatsuboshiToolkit` code, there may be some little differences between these two games

## Special Thanks
[MalitsPlus/HoshimiToolkit](https://github.com/MalitsPlus/HoshimiToolkit) 