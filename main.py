import os
import shutil
import time
import uuid
import json
import argparse
from datetime import datetime
# from pdf2image import convert_from_path
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF
from multiprocessing import Pool
from tqdm import tqdm  # For progress bar
import subprocess
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO
from datetime import datetime

# Global variable for parallelization control
DOCUMENT_CORES = 128  # Number of cores for document-level parallel processing

# 將 add_watermark_to_pdf 更改為使用 PyMuPDF 與 ReportLab 來產生透明浮水印疊加
def add_watermark_to_pdf(user, watermark_text, input_pdf, output_pdf, opacity=0.5, angle=45, spacing_width=100, spacing_height=100, font_size=72, progress_queue=None):
    # check if output_pdf exists
    if os.path.exists(output_pdf):
        print(f"\033[31m[{user}] {output_pdf} exists.\033[0m")
        return
    print(f"\033[37m[{user}] {input_pdf} start processing\033[0m")  # White for document start
    today_date = datetime.today().strftime("%Y-%m-%d")

    # Open the original PDF
    pdf_document = fitz.open(input_pdf)
    pdf_output = fitz.open()  # Create a blank target PDF

    for page_num in range(pdf_document.page_count):
        page = pdf_document.load_page(page_num)
        page_rect = page.rect  # Get current page dimensions

        # Create the watermark layer
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=(page_rect.width, page_rect.height))
        can.setFont("Helvetica", font_size)
        can.setFillColorRGB(0.6, 0.6, 0.6, alpha=opacity)  # Set opacity

        # Translate canvas for rotation, draw text grid, then restore
        can.saveState()
        can.translate(page_rect.width / 2, page_rect.height / 2)
        can.rotate(angle)
        
        # Draw watermark text in a staggered grid pattern
        text_width, text_height = can.stringWidth(watermark_text, "Helvetica", font_size), font_size
        x_offset = 0  # Initial horizontal offset

        for y in range(-int(page_rect.height), int(page_rect.height), int(text_height + spacing_height)):
            for x in range(-int(page_rect.width), int(page_rect.width), int(text_width + spacing_width)):
                can.drawString(x + x_offset, y, watermark_text)
            
            # Apply offset for the next row to create a staggered effect
            x_offset = (x_offset + int(text_width * 0.5)) % (text_width + spacing_width)

        can.restoreState()  # Reset the canvas state after drawing the rotated grid
        
        # Finalize the watermark layer and overlay on the PDF page
        can.save()
        packet.seek(0)
        watermark_pdf = fitz.open("pdf", packet.read())
        page.show_pdf_page(page_rect, watermark_pdf, 0)
        
        # Insert processed page into output PDF
        pdf_output.insert_pdf(pdf_document, from_page=page_num, to_page=page_num)

    # Update progress
    if progress_queue:
        progress_queue.put(1)  # Mark one page processed

    # Save the final file
    pdf_output.save(output_pdf)
    pdf_output.close()
    pdf_document.close()
    print(f"\033[34m[{user}] {output_pdf} saved with transparent overlay\033[0m")

        
# Collect all PDF documents for each user and apply combined user + document-level parallelization
# 主函數，平行處理所有用戶的 PDF 文件
def process_all_users_files(config):
    input_folder = config["PDF"]["Input Folder"]
    output_folder = config["PDF"]["Output Folder"]
    os.makedirs(output_folder, exist_ok=True)

    course_name = config["course_info"]["course_name"]
    term = config["course_info"]["term"]
    opacity = config["PDF"]["opacity"] / 255  # Adjust opacity to match reportlab's scale (0-1)
    angle = config["PDF"]["angle"]
    spacing_width = config["PDF"]["spacing_width"]
    spacing_height = config["PDF"]["spacing_height"]
    font_size = config["PDF"]["font_size"]

    # 建立用戶列表
    user_list = generate_user_list(config)
    all_pdf_tasks = []

    today_str = datetime.today().strftime("%Y-%m-%d")
    total_docs = 0

    # 處理每個用戶的文件
    for user in user_list:
        user_folder = os.path.join(output_folder, term, course_name, user)
        watermark_text = f"{user} {config['PDF']['watermark_text']}"
        
        for root, _, files in os.walk(input_folder):
            relative_path = os.path.relpath(root, input_folder)
            user_target_folder = os.path.join(user_folder, relative_path)
            os.makedirs(user_target_folder, exist_ok=True)
            
            for file in files:
                if file.lower().endswith(".pdf"):
                    input_pdf_path = os.path.join(root, file)
                    output_pdf_path = os.path.join(user_target_folder, file)
                    
                    # 收集任務
                    all_pdf_tasks.append((user, watermark_text, input_pdf_path, output_pdf_path, opacity, angle, spacing_width, spacing_height, font_size, None))
                    total_docs += 1
                    # add_watermark_to_pdf(user, watermark_text, input_pdf_path, output_pdf_path, opacity, angle, spacing_width, spacing_height, font_size)

    # 顯示進度條並平行處理
    # Display a progress bar 
    print("\033[33mStarting PDF processing...")  # Yellow for processing start
    print(f"========================================================")
    print(f"Number of users to process: {len(user_list)}")
    print(f"Total documents to process: {total_docs}")
    print(f"User list: {user_list}")
    print(f"========================================================")
    print(f"Now processing all documents with {DOCUMENT_CORES} cores...\n\033[0m")
    with tqdm(total=total_docs, desc="[PROGRESS]", unit="doc", colour="blue") as progress_bar:
        def update_progress_bar(*args):
            progress_bar.update(1)

        with Pool(processes=DOCUMENT_CORES) as pool:
            for task in all_pdf_tasks:
                pool.apply_async(add_watermark_to_pdf, task, callback=update_progress_bar)
            pool.close()
            pool.join()

# 生成用戶列表
def generate_user_list(config):
    course_name = config["course_info"]["course_name"]
    TA_from, TA_to = config["course_info"]["TA_from"], config["course_info"]["TA_to"]
    stu_from, stu_to = config["course_info"]["stu_from"], config["course_info"]["stu_to"]
    
    user_list = []
    if TA_from:
        user_list.extend([f"{course_name}TA{num:02d}" for num in range(TA_from, (TA_to or TA_from) + 1)])
    if stu_from:
        user_list.extend([f"{course_name}{num:03d}" for num in range(stu_from, (stu_to or stu_from) + 1)])
    return user_list
    

    # Final cleanup of the main `temp_images` folder after processing
    # if os.path.exists("temp_images"):
    #     shutil.rmtree("temp_images")
    #     print("Temporary folder 'temp_images' deleted after all processing.")

# Load configurations from JSON
def load_config(json_path):
    with open(json_path, 'r') as file:
        config = json.load(file)
    
    # copy to the history folder
    history_folder = "history"
    os.makedirs(history_folder, exist_ok=True)
    # combine the file name with the original name, course name and current date 
    history_file = os.path.join(history_folder, f"{os.path.splitext(os.path.basename(json_path))[0]}_{config['course_info']['course_name']}_{datetime.today().strftime('%Y-%m-%d')}.json")

    shutil.copyfile(json_path, history_file)
    return config
# Move the PDF files to the COURSE FOLDER
def move_files_to_course_folder(config):
    input_folder = config["PDF"]["Input Folder"]
    output_folder = config["PDF"]["Output Folder"]
    term = config["course_info"]["term"]
    course_name = config["course_info"]["course_name"]
    NFS_folder = config["PDF"]["NFS_folder"]


    user_list = []
    TA_from = config["course_info"]["TA_from"]
    TA_to = config["course_info"]["TA_to"]
    stu_from = config["course_info"]["stu_from"]
    stu_to = config["course_info"]["stu_to"]
    if TA_from != 0:
        if TA_to == 0:
            TA_to = TA_from
        user_list.extend([f"{course_name}TA{num:02d}" for num in range(TA_from, TA_to + 1)])
    if stu_from != 0:
        if stu_to == 0:
            stu_to = stu_from
        user_list.extend([f"{course_name}{num:03d}" for num in range(stu_from, stu_to + 1)])
    
    print(f"\033[33m========================================================")
    print(f"Starting moving PDF files to the course folder...")  # Yellow for processing start
    print(f"User list: {user_list}")
    print(f"========================================================\033[0m")

    for user in user_list:
        original_folder_name = input_folder.split("/")[-1]
        user_folder = os.path.join(output_folder, term, course_name, user)
        target_folder = os.path.join(NFS_folder, term, course_name, user, original_folder_name)
        # check if the user folder exists
        if not os.path.exists(user_folder):
            print(f"\033[31m[{user}] {user_folder} does not exist.\033[0m")
            continue
        # check if the folder exists
        if not os.path.exists(target_folder):
            os.makedirs(target_folder, exist_ok=True)

        print(f"[{user}] Moving {user_folder} to {target_folder}")
        if os.path.exists(f"{target_folder}"):
            shutil.rmtree(f"{target_folder}")
        # move the user folder to the target folder
        command = f"mv {user_folder}/ {target_folder}/"
        subprocess.call(command, shell=True)
        # change the permission of the target folder
        command = f"chmod -R 500 {target_folder}"
        subprocess.call(command, shell=True)
        # change the owner of the target folder
        command = f"chown -R {user}:{course_name} {target_folder}"
        subprocess.call(command, shell=True)
        
    print(f"\033[32m========================================================")
    print(f"All PDF files have been moved to the course folder successfully.")
    print(f"========================================================\033[0m")

def create_symbolic_link_to_user_folder(config):
    input_folder = config["PDF"]["Input Folder"]
    term = config["course_info"]["term"]
    course_name = config["course_info"]["course_name"]
    NFS_folder = config["PDF"]["NFS_folder"]    
    course_folder = config["PDF"]["course_folder"]

    user_list = []
    TA_from = config["course_info"]["TA_from"]
    TA_to = config["course_info"]["TA_to"]
    stu_from = config["course_info"]["stu_from"]
    stu_to = config["course_info"]["stu_to"]
    if TA_from != 0:
        if TA_to == 0:
            TA_to = TA_from
        user_list.extend([f"{course_name}TA{num:02d}" for num in range(TA_from, TA_to + 1)])
    if stu_from != 0:
        if stu_to == 0:
            stu_to = stu_from
        user_list.extend([f"{course_name}{num:03d}" for num in range(stu_from, stu_to + 1)])
    
    print(f"\033[33m========================================================")
    print(f"Starting creating symbolic link to the user folder...")  # Yellow for processing start
    print(f"User list: {user_list}")
    print(f"========================================================\033[0m")

    for user in user_list:
        original_folder_name = input_folder.split("/")[-1]
        target_folder = os.path.join(NFS_folder, term, course_name, user, original_folder_name)
        user_home_folder = os.path.join(course_folder, term, course_name, user)

        # check if the folder exists
        if not os.path.exists(target_folder):
            print(f"\033[31m[{user}] {target_folder} does not exist.\033[0m")
            continue


        # check if the symbolic link exists /RAID2/cshrc/.vscode --> ~/.vscode
        command = f"rm -rf {user_home_folder}/.vscode"
        subprocess.call(command, shell=True)
        # create a symbolic link to the user folder
        command = f"ln -s /RAID2/cshrc/.vscode {user_home_folder}/.vscode"
        subprocess.call(command, shell=True)
        # change the owner of the symbolic link
        command = f"chown -h {user}:{course_name} {user_home_folder}/.vscode"
        subprocess.call(command, shell=True)

        # check if the symbolic link exists
        command = f"unlink {user_home_folder}/Desktop/{original_folder_name}"
        subprocess.call(command, shell=True)
        # create a symbolic link to the user folder
        command = f"ln -s {target_folder} {user_home_folder}/Desktop/{original_folder_name}"
        subprocess.call(command, shell=True)
        # change the owner of the symbolic link
        command = f"chown -h {user}:{course_name} {user_home_folder}/Desktop/{original_folder_name}"
        subprocess.call(command, shell=True)
        print(f"[{user}] Create symbolic link {user_home_folder}/Desktop/{original_folder_name} to {target_folder}/{original_folder_name}")

        # Create /RAID2/PROCESS/ADFP folder link
        if "ADFP" in original_folder_name:
            command = f"unlink {user_home_folder}/Desktop/ADFP"
            subprocess.call(command, shell=True)
            command = f"ln -s /RAID2/PROCESS/ADFP {user_home_folder}/Desktop/ADFP"
            subprocess.call(command, shell=True)
            command = f"chown -h {user}:{course_name} {user_home_folder}/Desktop/ADFP"
            subprocess.call(command, shell=True)
            print(f"[{user}] Create symbolic link {user_home_folder}/Desktop/ADFP to /RAID2/PROCESS/ADFP")
        elif "TN7" in original_folder_name:
            command = f"unlink {user_home_folder}/Desktop/TN7"
            subprocess.call(command, shell=True)
            command = f"ln -s /RAID2/PROCESS/TN7 {user_home_folder}/Desktop/TN7"
            subprocess.call(command, shell=True)
            command = f"chown -h {user}:{course_name} {user_home_folder}/Desktop/TN7"
            subprocess.call(command, shell=True)
            print(f"[{user}] Create symbolic link {user_home_folder}/Desktop/TN7 to /RAID2/PROCESS/TN7")
        elif "TN16" in original_folder_name:
            command = f"unlink {user_home_folder}/Desktop/TN16"
            subprocess.call(command, shell=True)
            command = f"ln -s /RAID2/PROCESS/TN16FFC-P {user_home_folder}/Desktop/TN16"
            subprocess.call(command, shell=True)
            command = f"chown -h {user}:{course_name} {user_home_folder}/Desktop/TN16"
            subprocess.call(command, shell=True)
            print(f"[{user}] Create symbolic link {user_home_folder}/Desktop/TN16 to /RAID2/PROCESS/TN16FFC-P")
        elif "U18" in original_folder_name:
            command = f"unlink {user_home_folder}/Desktop/U18"
            subprocess.call(command, shell=True)
            command = f"ln -s /RAID2/PROCESS/U18 {user_home_folder}/Desktop/U18"
            subprocess.call(command, shell=True)
            command = f"chown -h {user}:{course_name} {user_home_folder}/Desktop/U18"
            subprocess.call(command, shell=True)
            print(f"[{user}] Create symbolic link {user_home_folder}/Desktop/U18 to /RAID2/PROCESS/U18")
        
    print(f"\033[32m========================================================")
    print(f"All symbolic links have been created successfully.")
    print(f"========================================================\033[0m")

def remove_files_from_output_folder(config):
    output_folder = config["PDF"]["Output Folder"]
    term = config["course_info"]["term"]
    course_name = config["course_info"]["course_name"]

    remove_folder = os.path.join(output_folder, term, course_name)
    print(f"\033[33m========================================================")
    print(f"Starting removing PDF files from the output folder...")  # Yellow for processing start
    print(f"Remove folder: {remove_folder}")
    print(f"========================================================\033[0m")

    if os.path.exists(remove_folder):
        shutil.rmtree(remove_folder)
        print(f"\033[32m========================================================")
        print(f"All PDF files have been removed from the output folder successfully.")
        print(f"========================================================\033[0m")
    else:
        print(f"\033[31m========================================================")
        print(f"No PDF files found in the output folder.")
        print(f"========================================================\033[0m")

def remove_files_from_NFS_folder(config):
    NFS_folder = config["PDF"]["NFS_folder"]
    term = config["course_info"]["term"]
    course_name = config["course_info"]["course_name"]

    remove_folder = os.path.join(NFS_folder, term, course_name)
    print(f"\033[33m========================================================")
    print(f"Starting removing PDF files from the NFS folder...")  # Yellow for processing start
    print(f"Remove folder: {remove_folder}")
    print(f"========================================================\033[0m")

    if os.path.exists(remove_folder):
        shutil.rmtree(remove_folder)
        print(f"\033[32m========================================================")
        print(f"All PDF files have been removed from the NFS folder successfully.")
        print(f"========================================================\033[0m")
    else:
        print(f"\033[31m========================================================")
        print(f"No PDF files found in the NFS folder.")
        print(f"========================================================\033[0m")



# Main entry function
def main():
    parser = argparse.ArgumentParser(description="Watermark PDF documents based on configuration JSON.")
    parser.add_argument('-r', '--read', help="Specify the JSON configuration file path.", required=True)
    args = parser.parse_args()
    print(f"\033[33m========================================================")
    config = load_config(args.read)


    if config["PDF"]["Enable_WaterProof_PDF"]:
        start_time = time.time()
        process_all_users_files(config)
        elapsed_time = time.time() - start_time
        print(f"\033[32m========================================================\033[0m")
        print(f"\033[32m All PDF files have been processed successfully.\033[0m")
        print(f"\033[32m Total time elapsed: {elapsed_time:.2f} seconds.\033[0m")
        print(f"\033[32m========================================================\033[0m")

    if config["PDF"]["Enable_Move_PDF_To_NFS"]:
        move_files_to_course_folder(config)
    
    if config["PDF"]["Enable_Create_Symbolic_Link"]:
        create_symbolic_link_to_user_folder(config)
    
    if config["PDF"]["Enable_Remove_from_output_folder"]:
        remove_files_from_output_folder(config)

    if config["PDF"]["Enable_Remove_from_NFS_folder"]:
        remove_files_from_NFS_folder(config)
        
if __name__ == "__main__":
    main()
