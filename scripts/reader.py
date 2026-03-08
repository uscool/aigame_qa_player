import os

# this is intended to be the runner script. This will select and create models for new 
# games. Alternatively, it allows older game AI models to be loaded and used to run 
# automated testing. 

# Core functionality planned:
# 1. Create models for game training (call file)
# 2. Load existing models for automated testing (call file)
# 3. Pull existing dashboards for game performance. (Terminal based)
#     - Checkboxes recommended for which dashboards to pull (training, testing, etc.)
#     - Runtime dash
#     - Training dash

model=input("Enter the model name (please enter NULL if it does not exist): ")
if model != "NULL":
    # CALLS NEW MODEL SCRIPT
    print(f"Creating new model: {model}")
    new_model=input("Enter the name of the new game: ")
    os.system(f"python new_model.py {model} {new_model}")
else:
    # CALLS EXISTING MODEL SCRIPT
    print("Loading existing model...")
    os.system(f"python load_model.py {model}")