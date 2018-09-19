import argparse
import collections
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import openslide
import pandas
# from planar import BoundingBox, Vec2
from pymongo import MongoClient, errors
from shapely.geometry import Polygon, Point, MultiPoint
from skimage.color import separate_stains, hed_from_rgb


def assure_path_exists(path):
    """
    If path exists, great.
    If not, then create it.
    :param path:
    :return:
    """
    m_dir = os.path.dirname(path)
    if not os.path.exists(m_dir):
        os.makedirs(m_dir)


def mongodb_connect(client_uri):
    """
    Connection routine
    :param client_uri:
    :return:
    """
    try:
        return MongoClient(client_uri, serverSelectionTimeoutMS=1)
    except errors.ConnectionFailure:
        print("Failed to connect to server {}".format(client_uri))
        exit(1)


def get_file_list(substr, filepath):
    """
    Find lines in data file containing (case_id) substring.
    Return list.
    :param substr:
    :param filepath:
    :return:
    """
    lines = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if substr in line:
                lines.append(line)
    f.close()
    return lines


def copy_src_data(dest):
    """
    Copy data from nfs location to computation node.
    :param dest:
    :return:
    """
    # Get list of csv files containing features for this case_id
    for csv_dir1 in DATA_FILE_SUBFOLDERS:
        source_dir = os.path.join(DATA_FILE_FOLDER, csv_dir1)
        # copy all *.json and *features.csv files
        m_args = list(["rsync", "-ar", "--include", "*features.csv", "--include", "*.json"])
        # m_args = list(["rsync", "-avz", "--include", "*features.csv", "--include", "*.json"])
        m_args.append(source_dir)
        m_args.append(dest)
        print("executing " + ' '.join(m_args))
        subprocess.call(m_args)

    # Get slide
    my_file = Path(os.path.join(dest, (CASE_ID + '.svs')))
    if not my_file.is_file():
        svs_list = get_file_list(CASE_ID, 'config/image_path.list')
        svs_path = os.path.join(SVS_IMAGE_FOLDER, svs_list[0])
        print("executing scp", svs_path, dest)
        subprocess.check_call(['scp', svs_path, dest])


def get_tumor_markup(user_name):
    """
    Find what the pathologist circled as tumor.
    :param user_name:
    :return:
    """
    tumor_markup_list = []
    execution_id = (user_name + "_Tumor_Region")
    try:
        client = mongodb_connect('mongodb://' + DB_HOST + ':27017')
        client.server_info()  # force connection, trigger error to be caught
        db = client.quip
        coll = db.objects
        filter_q = {
            'provenance.image.case_id': CASE_ID,
            'provenance.analysis.execution_id': execution_id
        }
        projection_q = {
            'geometry.coordinates': 1,
            '_id': 0
        }
        print('quip.objects')
        print(filter_q, ',', projection_q)
        cursor = coll.find(filter_q, projection_q)
        for item in cursor:
            # geometry.coordinates happens to be a list with one thing in it: a list! (of point coordinates).
            temp = item['geometry']['coordinates']  # [ [ [ x, y ], ... ] ]
            points = temp[0]  # [ [x, y ], ... ]
            tumor_markup_list.append(points)
        client.close()
    except errors.ServerSelectionTimeoutError as err:
        print('Error in get_tumor_markup', err)
        exit(1)

    count = len(tumor_markup_list)
    if count == 0:
        print('No tumor markups were generated by ', user_name)
        exit(1)

    print('Tumor markup count: ', count)
    return tumor_markup_list


def markup_to_polygons(markup_list):
    """
    Clean up and convert to something we can use.
    :param markup_list:
    :return:
    """
    m_poly_list = []
    try:
        # roll through our list of lists
        for coordinates in markup_list:
            points_list = []
            # convert the point coordinates to Points
            for m_point in coordinates:
                m_point = Point(m_point[0], m_point[1])
                # print('m_point', m_point)  # normalized
                points_list.append(m_point)
            # create a Polygon
            m = MultiPoint(points_list)
            m_polygon = Polygon(m)
            # append to return-list
            m_poly_list.append(m_polygon)
    except Exception as ex:
        print('Error in convert_to_polygons', ex)
        exit(1)

    # Return list of polygons
    return m_poly_list


def string_to_polygon(poly_data, imw, imh, normalize):
    """
    Convert Polygon string to polygon
    :param poly_data:
    :param imw:
    :param imh:
    :param normalize:
    :return:
    """
    points_list = []

    tmp_str = str(poly_data)
    tmp_str = tmp_str.replace('[', '')
    tmp_str = tmp_str.replace(']', '')
    split_str = tmp_str.split(':')
    m_polygon = {}

    try:
        # Get list of points
        for i in range(0, len(split_str) - 1, 2):
            a = float(split_str[i])
            b = float(split_str[i + 1])
            if normalize:
                # Normalize points
                point = [a / float(imw), b / float(imh)]
            else:
                point = [a, b]
            m_point = Point(point)
            points_list.append(m_point)
        # Create a Polygon
        m = MultiPoint(points_list)
        m_polygon = Polygon(m)
    except Exception as ex:
        print('Error in string_to_polygon', ex)
        exit(1)

    return m_polygon


def get_data_files():
    """
    Return 2 lists containing full paths for CSVs and JSONs.
    :return:
    """
    filenames = os.listdir(SLIDE_DIR)  # get all files' and folders' names in directory

    folders = []
    for filename in filenames:  # loop through all the files and folders
        ppath = os.path.join(os.path.abspath(SLIDE_DIR), filename)
        if os.path.isdir(ppath):  # check whether the current object is a folder or not
            folders.append(ppath)

    folders.sort()
    # print('subfolders: ', len(folders))

    json_files = []
    csv_files = []
    for index, filename in enumerate(folders):
        # print(index, filename)
        files = os.listdir(filename)
        for name in files:
            ppath = os.path.join(os.path.abspath(filename), name)
            if name.endswith('json'):
                json_files.append(ppath)
            elif name.endswith('csv'):
                csv_files.append(ppath)

    # print('json_files: ', len(json_files))
    # print('csv_files: ', len(csv_files))

    json_files.sort()
    csv_files.sort()
    return json_files, csv_files


def get_poly_within(jfiles, tumor_list):
    """
    Identify only the files within the tumor regions
    :param jfiles:
    :param tumor_list:
    :return:
    """
    # print('files len: ', len(jfiles))
    # print('tumor_list len: ', len(tumor_list))
    temp = {}
    path_poly = {}
    # rtn_jfiles = []
    rtn_obj = {}
    # start_time = time.time()

    # Collect data
    z = set()
    count = 0
    for jfile in jfiles:
        with open(jfile, 'r') as f:
            # Read JSON data into the json_dict variable
            json_dict = json.load(f)
            # str = json_dict['out_file_prefix']
            imw = json_dict['image_width']
            imh = json_dict['image_height']
            tile_height = json_dict['tile_height']
            tile_width = json_dict['tile_width']
            tile_minx = json_dict['tile_minx']
            tile_miny = json_dict['tile_miny']
            fp = json_dict['out_file_prefix']

            item = 'x' + str(tile_minx) + '_' + 'y' + str(tile_miny)
            if item not in z:  # If the object is not in the list yet...
                inc_x = tile_minx + tile_width
                inc_y = tile_miny + tile_height
                # Create polygon for comparison
                point1 = Point(float(tile_minx) / float(imw), float(tile_miny) / float(imh))
                # print('point1', point1)  # normalized
                point2 = Point(float(inc_x) / float(imw), float(tile_miny) / float(imh))
                point3 = Point(float(inc_x) / float(imw), float(inc_y) / float(imh))
                point4 = Point(float(tile_minx) / float(imw), float(inc_y) / float(imh))
                point5 = Point(float(tile_minx) / float(imw), float(tile_miny) / float(imh))
                m = MultiPoint([point1, point2, point3, point4, point5])
                polygon = Polygon(m)
                # Map data file location (prefix) to bbox polygon
                # path_poly[f.name[:-pos]] = polygon
                path_poly[item] = {'poly': polygon, 'image_width': imw, 'image_height': imh, 'tile_width': tile_width,
                                   'tile_height': tile_height, 'tile_minx': tile_minx, 'tile_miny': tile_miny,
                                   'out_file_prefix': fp}
            else:
                count += 1

            z.add(item)

        f.close()
        temp.update(path_poly)

    print('dupes', count)
    print('len', len(temp))

    for tumor_roi in tumor_list:
        for key, val in temp.items():
            gotone = False
            p = val['poly']
            if p.within(tumor_roi):
                gotone = True
            elif p.intersects(tumor_roi):
                gotone = True
            elif tumor_roi.within(p):
                gotone = True
            elif tumor_roi.intersects(p):
                gotone = True
            if gotone:
                # print('val', val)
                rtn_obj.update({key: val})

    # elapsed_time = time.time() - start_time
    # print('Runtime get_poly_within: ')
    # print(time.strftime("%H:%M:%S", time.gmtime(elapsed_time)))

    # return rtn_jfiles
    return rtn_obj


def aggregate_data(jfile_objs, CSV_FILES):
    """
    Get data
    :param jfile_objs:
    :param CSV_FILES
    :return:
    """
    start_time = time.time()
    obj_map = {}
    obj_map1 = {}
    rtn_dict = {}

    for k, v in jfile_objs.items():
        filelist = []
        for ff in CSV_FILES:
            if k in ff:
                filelist.append(ff)

        data_obj = {'filelist': filelist, "image_width": v['image_width'], "image_height": v['image_height'],
                    "tile_height": v['tile_height'], "tile_width": v['tile_width'], "tile_minx": v['tile_minx'],
                    "tile_miny": v['tile_miny']}
        obj_map.update({k: data_obj})

    print('obj_map', len(obj_map))
    print('Aggregating csv data...')

    for k, v in obj_map.items():
        frames = []
        for ff in v['filelist']:
            df = pandas.read_csv(ff)
            # print('df.shape[0]: ', df.shape[0])
            if df.empty:
                # print('empty!')
                # print(len(v['filelist']))
                # print(k)
                # print(ff)
                continue
            else:
                # new = old[['A', 'C', 'D']].copy()
                df1 = df[
                    ['Perimeter', 'Flatness', 'Circularity', 'r_GradientMean', 'b_GradientMean',
                     'b_cytoIntensityMean', 'r_cytoIntensityMean', 'r_IntensityMean', 'r_cytoGradientMean',
                     'Elongation', 'Polygon']].copy()
                frames.append(df1)

        if frames:
            result = pandas.concat(frames)
            data_obj1 = {'df': result, "image_width": v['image_width'], "image_height": v['image_height'],
                         "tile_height": v['tile_height'], "tile_width": v['tile_width'], "tile_minx": v['tile_minx'],
                         "tile_miny": v['tile_miny']}

            obj_map1[ff] = data_obj1

        # Add to return variable
        rtn_dict.update(obj_map1)

    elapsed_time = time.time() - start_time
    print('Runtime aggregate_data: ')
    print(time.strftime("%H:%M:%S", time.gmtime(elapsed_time)))

    return rtn_dict


def get_mongo_doc(slide, patch_data):
    """
    Return a default mongo doc
    :param slide:
    :param patch_data:
    :return:
    """
    # TODO:!

    # Ratio of nuclear material
    percent_nuclear_material = float((patch_data['nucleus_area'] / (PATCH_SIZE * PATCH_SIZE)) * 100)
    # print("Ratio of nuclear material: ", percent_nuclear_material)

    patch_index = patch_data['patch_num']

    mydoc = {
        "case_id": CASE_ID,
        "image_width": image_width,
        "image_height": image_height,
        "mpp_x": mpp_x,
        "mpp_y": mpp_y,
        "user": USER_NAME,
        "tumorFlag": "tumor",
        "patch_index": patch_index,
        "patch_min_x_pixel": patch_data['patch_minx'],
        "patch_min_y_pixel": patch_data['patch_miny'],
        "patch_size": PATCH_SIZE,
        "patch_polygon_area": patch_polygon_area,
        "nucleus_area": patch_data['nucleus_area'],
        "percent_nuclear_material": percent_nuclear_material,
        # "patch_area_selected_percentage": 100.0,
        "grayscale_patch_mean": 0.0,
        "grayscale_patch_std": 0.0,
        "hematoxylin_patch_mean": 0.0,
        "hematoxylin_patch_std": 0.0,
        "grayscale_segment_mean": "n/a",
        "grayscale_segment_std": "n/a",
        "hematoxylin_segment_mean": "n/a",
        "hematoxylin_segment_std": "n/a",
        "flatness_segment_mean": "n/a",
        "flatness_segment_std": "n/a",
        "perimeter_segment_mean": "n/a",
        "perimeter_segment_std": "n/a",
        "circularity_segment_mean": "n/a",
        "circularity_segment_std": "n/a",
        "r_GradientMean_segment_mean": "n/a",
        "r_GradientMean_segment_std": "n/a",
        "b_GradientMean_segment_mean": "n/a",
        "b_GradientMean_segment_std": "n/a",
        "r_cytoIntensityMean_segment_mean": "n/a",
        "r_cytoIntensityMean_segment_std": "n/a",
        "b_cytoIntensityMean_segment_mean": "n/a",
        "b_cytoIntensityMean_segment_std": "n/a",
        "elongation_segment_mean": "n/a",
        "elongation_segment_std": "n/a",
        "tile_minx": patch_data['tile_minx'],
        "tile_miny": patch_data['tile_miny'],
        "datetime": datetime.now()
    }

    return mydoc


def update_db(slide, patch_data, db_name):
    """
    Write data, per patch.
    :param slide:
    :param patch_data:
    :param db_name:
    :return:
    """

    df = patch_data['df']

    mydoc = get_mongo_doc(slide, patch_data)

    # read_region returns an RGBA Image (PIL)
    patch = slide.read_region((patch_data['patch_minx'], patch_data['patch_miny']), 0, (PATCH_SIZE, PATCH_SIZE))

    # Histology
    mydoc = patch_operations(patch, mydoc)

    mycol = DB[db_name + '_features_td']  # name
    # Connect to MongoDB
    # try:
    #     client = mongodb_connect('mongodb://' + DB_HOST + ':27017')
    #     client.server_info()  # force connection, trigger error to be caught
    #     db = client.quip_comp
    #     mycol = db[db_name + '_features_td']  # name
    # except Exception as e:
    #     print('Connection error: ', e)
    #     exit(1)

    try:
        if not df.empty:
            mydoc['flatness_segment_mean'] = df['Flatness'].mean()
            mydoc['flatness_segment_std'] = df['Flatness'].std()
            mydoc['perimeter_segment_mean'] = df['Perimeter'].mean()
            mydoc['perimeter_segment_std'] = df['Perimeter'].std()
            mydoc['circularity_segment_mean'] = df['Circularity'].mean()
            mydoc['circularity_segment_std'] = df['Circularity'].std()
            mydoc['r_GradientMean_segment_mean'] = df['r_GradientMean'].mean()
            mydoc['r_GradientMean_segment_std'] = df['r_GradientMean'].std()
            mydoc['b_GradientMean_segment_mean'] = df['b_GradientMean'].mean()
            mydoc['b_GradientMean_segment_std'] = df['b_GradientMean'].std()
            mydoc['r_cytoIntensityMean_segment_mean'] = df['r_cytoIntensityMean'].mean()
            mydoc['r_cytoIntensityMean_segment_std'] = df['r_cytoIntensityMean'].std()
            mydoc['b_cytoIntensityMean_segment_mean'] = df['b_cytoIntensityMean'].mean()
            mydoc['b_cytoIntensityMean_segment_std'] = df['b_cytoIntensityMean'].std()
            mydoc['elongation_segment_mean'] = df['Elongation'].mean()
            mydoc['elongation_segment_std'] = df['Elongation'].std()

        # Insert record in either case
        mycol.insert_one(mydoc)

    except Exception as err:
        print('update_db error: ', err)
        exit(1)
    # print('mydoc', json.dumps(mydoc, indent=4, sort_keys=True))


def calculate(tile_data):
    """
    Mean and std of Perimeter, Flatness, Circularity,
    r_GradientMean, b_GradientMean, b_cytoIntensityMean, r_cytoIntensityMean.
    :param tile_data:
    :return:
    """
    p = Path(os.path.join(SLIDE_DIR, (CASE_ID + '.svs')))
    print('Reading slide...')
    start_time = time.time()
    slide = openslide.OpenSlide(str(p))

    elapsed_time = time.time() - start_time
    print('Time it takes to read slide: ', elapsed_time)
    start_time = time.time()  # reset

    # Iterate through tile data
    for key, val in tile_data.items():
        # Create patches
        do_tiles(val, slide)
        # exit(0)  # TESTING ONE.

    slide.close()

    elapsed_time = time.time() - start_time
    print('Runtime calculate: ')
    print(time.strftime("%H:%M:%S", time.gmtime(elapsed_time)))


def rgb_to_stain(rgb_img_matrix, sizex, sizey):
    """
    RGB to stain color space conversion
    :param rgb_img_matrix:
    :param sizex:
    :param sizey:
    :return:
    """
    hed_title_img = separate_stains(rgb_img_matrix, hed_from_rgb)
    hematoxylin_img_array = [[0 for x in range(sizex)] for y in range(sizey)]
    for index1, row in enumerate(hed_title_img):
        for index2, pixel in enumerate(row):
            hematoxylin_img_array[index1][index2] = pixel[0]

    return hematoxylin_img_array


def patch_operations(patch, mydoc):
    # Convert to grayscale
    img = patch.convert('L')
    # img to array
    img_array = np.array(img)
    # Intensity for all pixels, divided by num pixels
    mydoc['grayscale_patch_mean'] = np.mean(img_array)
    mydoc['grayscale_patch_std'] = np.std(img_array)
    # Intensity for all pixels inside segmented objects...
    # mydoc.grayscale_segment_mean = "n/a"
    # mydoc.grayscale_segment_std = "n/a"

    # Convert to RGB
    img = patch.convert('RGB')
    img_array = np.array(img)
    hed_title_img = separate_stains(img_array, hed_from_rgb)
    max1 = np.max(hed_title_img)
    min1 = np.min(hed_title_img)
    new_img_array = hed_title_img[:, :, 0]
    new_img_array = ((new_img_array - min1) * 255 / (max1 - min1)).astype(np.uint8)
    mydoc['hematoxylin_patch_mean'] = np.mean(new_img_array)
    mydoc['hematoxylin_patch_std'] = np.std(new_img_array)
    # mydoc.Hematoxylin_segment_mean = "n/a"
    # mydoc.Hematoxylin_segment_std = "n/a"

    return mydoc


def tile_operations(patch, type, name_prefix, w, h):
    """

    :param patch:
    :param type:
    :param name_prefix:
    :param w:
    :param h:
    :return:
    """
    data = {}

    img = patch.convert(type)

    # img to array
    img_array = np.array(img)

    if name_prefix == 'hematoxylin':
        # Convert rgb to stain color space
        img_array = rgb_to_stain(img_array, w, h)

    # average of the array elements
    patch_mean = np.mean(img_array)
    data[name_prefix + '_patch_mean'] = patch_mean

    # standard deviation of the array elements
    patch_std = np.std(img_array)
    data[name_prefix + '_patch_std'] = patch_std

    percentiles = [10, 25, 50, 75, 90]
    for i in range(len(percentiles)):
        name = name_prefix + '_patch_percentile_' + str(percentiles[i])
        data[name] = np.percentile(img_array, percentiles[i])
        # print(name_prefix + " patch {} percentile: {}".format(percentiles[i],
        # np.percentile(img_array, percentiles[i])))

    return data


def histology(slide, min_x, min_y, w, h):
    """

    :param slide:
    :param min_x:
    :param min_y:
    :param w:
    :param h:
    :return:
    """
    rtn_obj = {}
    try:
        # read_region returns an RGBA Image (PIL)
        tile = slide.read_region((min_x, min_y), 0, (w, h))

        # convert image and perform calculations
        a = tile_operations(tile, 'L', 'grayscale', w, h)
        b = tile_operations(tile, 'RGB', 'hematoxylin', w, h)
        c = {}

        for (key, value) in a.items():
            c.update({key: value})

        for (key, value) in b.items():
            c.update({key: value})

        rtn_obj = c

    except Exception as e:
        print('Error reading region: ', min_x, min_y)
        print(e)
        exit(1)

    return rtn_obj


def detect_bright_spots(gray):
    """
    Detect bright spots (no staining) and ignore those areas in area computation
    :param gray:
    :return:
    """
    # load the image, convert it to grayscale, and blur it
    # image = cv2.imread('img/detect_bright_spots.png')
    # gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    # Pixel values p >= 200 are set to 255 (white)
    # Pixel values < 200 are set to 0 (black).
    thresh = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY)[1]

    # Do something.


def do_tiles(data, slide):
    """
    Divide tile into patches
    :param data:
    :return:
    """
    print('Dividing patch into tiles...')
    start_time = time.time()

    patch_num = 0
    df = data['df']
    width = data['tile_width']
    height = data['tile_height']
    cols = width / PATCH_SIZE
    rows = height / PATCH_SIZE
    # data_complete = {}

    # Divide tile into patches
    for x in range(1, (int(cols) + 1)):
        for y in range(1, (int(rows) + 1)):
            patch_num += 1
            print('patch_num', patch_num)
            # minx = minx + (x * tile_size)
            # miny = miny + (y * tile_size)
            minx = x * PATCH_SIZE
            miny = y * PATCH_SIZE
            minx = minx + data['tile_minx']
            miny = miny + data['tile_miny']
            maxx = minx + PATCH_SIZE
            maxy = miny + PATCH_SIZE

            # Normalize
            nminx = minx / image_width
            nminy = miny / image_width
            nmaxx = maxx / image_width
            nmaxy = maxy / image_width

            # Bounding box representing patch
            print((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy))
            # bbox = BoundingBox([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
            bbox = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)])
            bbox1 = Polygon([(nminx, nminy), (nmaxx, nminy), (nmaxx, nmaxy), (nminx, nmaxy), (nminx, nminy)])

            df2 = pandas.DataFrame()
            nucleus_area = 0.0
            # Figure out which polygons (data rows) belong to which patch
            for index, row in df.iterrows():
                xy = row['Polygon']
                polygon_shape = string_to_polygon(xy, data['image_width'], data['image_height'], False)
                polygon_shape = polygon_shape.buffer(0.0)  # Using a zero-width buffer cleans up many topology problems
                # polygon_shape1 = string_to_polygon(xy, data['image_width'], data['image_height'], True)
                # polygon_shape1 = polygon_shape1.buffer(0.0)

                # print('polygon_shape', polygon_shape)

                # Accumulate information
                if polygon_shape.within(bbox) or polygon_shape.intersects(bbox):
                    df2 = df2.append(row)
                    if polygon_shape.intersects(bbox):
                        try:
                            nucleus_area += polygon_shape.intersection(bbox).area
                            # nucleus_area += polygon_shape1.intersection(bbox1).area
                            # print(nucleus_area * factor)
                        except Exception as err:
                            # except errors.TopologicalError as toperr:
                            print('Invalid geometry', err)
                    else:
                        nucleus_area += polygon_shape.area
                        # nucleus_area += polygon_shape1.area
                        # print(nucleus_area * factor)

            nucleus_area = nucleus_area / PATCH_SIZE
            print('nucleus_area', nucleus_area)

            update_db(slide, {'df': df2, 'nucleus_area': nucleus_area, 'patch_num': patch_num,
                              'patch_minx': minx, 'patch_miny': miny, 'tile_minx': data['tile_minx'],
                              'tile_miny': data['tile_miny'], 'image_width': data['image_width'],
                              'image_height': data['image_height']}, coll_name)

    elapsed_time = time.time() - start_time
    print('Runtime do_tiles: ')
    print(time.strftime("%H:%M:%S", time.gmtime(elapsed_time)))
    # exit(0)  # testing one tile


def get_image_metadata():
    p = Path(os.path.join(SLIDE_DIR, (CASE_ID + '.svs')))
    slide = openslide.OpenSlide(str(p))
    mpp_x = slide.properties[openslide.PROPERTY_NAME_MPP_X]
    mpp_y = slide.properties[openslide.PROPERTY_NAME_MPP_Y]
    mpp_x = round(float(mpp_x), 4)
    mpp_y = round(float(mpp_y), 4)
    image_width, image_height = slide.dimensions
    # image_width = slide.dimensions[0]
    # image_height = slide.dimensions[1]
    slide.close()

    return mpp_x, mpp_y, image_width, image_height


# constant variables
WORK_DIR = "/data1/tdiprima/dataset"
DATA_FILE_FOLDER = "nfs004:/data/shared/bwang/composite_dataset"
SVS_IMAGE_FOLDER = "nfs001:/data/shared/tcga_analysis/seer_data/images"

# construct the argument parser and parse the arguments
ap = argparse.ArgumentParser()
ap.add_argument("-s", "--slide_name", help="svs image name")
ap.add_argument("-u", "--user_name", help="user who identified tumor regions")
ap.add_argument("-b", "--db_host", help="database host")
ap.add_argument("-p", "--patch_size", type=int, help="patch size")
args = vars(ap.parse_args())
print(args)

if not len(sys.argv) > 1:
    program_name = sys.argv[0]
    lst = ['python', program_name, '-h']
    subprocess.call(lst)  # Show help
    exit(1)

CASE_ID = args["slide_name"]
USER_NAME = args["user_name"]
PATCH_SIZE = args["patch_size"]
DB_HOST = args["db_host"]

SLIDE_DIR = os.path.join(WORK_DIR, CASE_ID) + os.sep
DATA_FILE_SUBFOLDERS = get_file_list(CASE_ID, 'config/data_file_path.list')
# print('DATA_FILE_SUBFOLDERS', DATA_FILE_SUBFOLDERS)

# Fetch data.
assure_path_exists(SLIDE_DIR)
copy_src_data(SLIDE_DIR)

mpp_x, mpp_y, image_width, image_height = get_image_metadata()
patch_polygon_area = PATCH_SIZE * PATCH_SIZE * mpp_x * mpp_y
print('patch_polygon_area', patch_polygon_area)

# Find what the pathologist circled as tumor.
tumor_mark_list = get_tumor_markup(USER_NAME)
# print('tumor_mark_list', len(tumor_mark_list))

# List of Tumor polygons
tumor_poly_list = markup_to_polygons(tumor_mark_list)
# print('tumor_poly_list', len(tumor_poly_list))

# Fetch list of data files
JSON_FILES, CSV_FILES = get_data_files()

# Identify only the files within the tumor regions
jfile_objs = get_poly_within(JSON_FILES, tumor_poly_list)
print('get_poly_within len: ', len(jfile_objs))

# Get data
csv_data = aggregate_data(jfile_objs, CSV_FILES)
print('csv_data len: ', len(csv_data))

# Connect to MongoDB
coll_name = 'test1'
client = {}
try:
    client = mongodb_connect('mongodb://' + DB_HOST + ':27017')
    client.server_info()  # force connection, trigger error to be caught
    DB = client.quip_comp
except Exception as e:
    print('Connection error: ', e)
    exit(1)

# Calculate
calculate(csv_data)

client.close()

exit(0)
