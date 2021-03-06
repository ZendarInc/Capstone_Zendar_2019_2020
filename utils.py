import os
import cv2
import pyproj
import pickle
import numpy as np
from PIL import Image
from pykml import parser
from copy import deepcopy
import scipy.stats as stat
import matplotlib.pyplot as plt
from sklearn.cluster import DBSCAN
from pykml.factory import KML_ElementMaker as kml
from scipy.spatial.transform import Rotation as rot

def projection(position_ref, attitude_ref, pos, att=None):
    """ Project in a plane defined by attitude_ref and position_ref """
    trans = attitude_ref.apply(pos-position_ref)
    trans[2] = 0
    new_pos = position_ref + attitude_ref.apply(trans, True)
    if not att is None:
        new_att = rotation_proj(attitude_ref, att).inv()*attitude_ref
        return new_pos, new_att
    else:
        return new_pos

def data_projection(position_ref, attitude_ref, data):
    """ Project in a plane defined by attitude_ref and position_ref """
    new_pos, new_att = projection(position_ref, attitude_ref, data.gps_pos, data.attitude)
    new_data = deepcopy(data)
    new_data.gps_pos, new_data.attitude = new_pos, new_att
    return new_data

def change_attributes_frame(img):
    """ Change attributes to CV2 (right-back-down) frame and position in top left corner """
    # Attitude conversion
    r0 = rot.from_quat(list((img.attrs['ATTITUDE'][0][1],
                                 img.attrs['ATTITUDE'][0][2],
                                 img.attrs['ATTITUDE'][0][3],
                                 img.attrs['ATTITUDE'][0][0]))) # From POV(up, left) to ECEF
    r0_inv = r0.inv()                               # from ECEF to POV(up,left)
    r1 = rot.from_dcm([[0,-1,0],[-1,0,0],[0,0,-1]]) # from POV to CV2 Coordinate(right,down)
    r2 = r1*r0_inv                                  # from ECEF to CV2, new ATTITUDE!
    new_quat = r2.as_quat()                         # new ATTITUDE to be saved!
    
    # Position conversion
    p_bottomright_global = list(img.attrs['POSITION'][0])     #bottom right in ECEF
    p_topleft_global = p_bottomright_global + r0.apply([20,30,0])   #topleft in ECEF, new POSITION!
    return new_quat, p_topleft_global

def preprocessor(img):
    """ Handle the preprocessor function if defined in main.py """
    return DBSCAN_filter(img, kernel=(9,9), scale=0, binary=False)

def increase_contrast(img, lin_coeff, threshold, offset):
    """ Increase contrast in the image """
    mask = (img >= threshold)
    img = np.multiply(mask, lin_coeff*img + offset) + np.multiply(np.logical_not(mask), img)
    img[img >= 255] = 255
    return img.astype(np.uint8)

def DBSCAN_filter(im, kernel, scale, eps=5, min_samples=30, binary=True):
    """ Filter images to binary based on DBSCAN clustering """
    blur1 = cv2.GaussianBlur(im, kernel, scale)
    ret1,th1 = cv2.threshold(blur1, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    db = DBSCAN(eps, min_samples).fit(np.transpose(np.nonzero(th1)))
    np.place(th1, th1, db.labels_ > -1)
    if binary:        
        return (255*th1).astype(np.uint8)
    else:
        return np.multiply(im, th1).astype(np.uint8)

def increase_saturation(img):
    """ Increase saturation of an image """
    sat = 1.7
    gamma = 1.2
    im = np.power(sat*img/255, gamma)*255
    im[im >= 255] = 255
    return im.astype(np.uint8)

def figure_save(number, name):
    os.makedirs(os.path.dirname("Figures/"+str(name)+'.pickle'), exist_ok=True)
    pickle.dump(plt.figure(number), open("Figures/"+str(name)+'.pickle', 'wb'))

def import_figure(name):
    fig = pickle.load(open("Figures/"+str(name)+'.pickle', 'rb'))
    fig.show()

def stat_test(Y, Yhat, S, p):
    """ Perform statistical test to reject outliers """
    used = np.zeros(len(Y))
    for i in range(0, len(Yhat)):
        if (Yhat[i] - Y[i])**2/S[i][i] <= stat.chi2.ppf(p, df=1):
            used[i] = Yhat[i] - Y[i]
    return used  

def stat_filter(x, p):
    """ Filter outliers from a sequence """
    mean = np.mean(x, axis = 0)
    std = np.std(x, axis = 0)
    out =  []
    for i in range(len(x)):
        out.append(np.multiply(x[i],np.greater_equal(stat.chi2.ppf(p, df=1), np.square(np.divide(x[i] - mean,std)))))
    return out

def export_kml(filename, pos):
    """ Export position in LLA to KML """
    fld = kml.Document()
    for i in range(len(pos)):
        fld.append(kml.Placemark(kml.Point(kml.coordinates(str(pos[0])+","+str(pos[1])+","+str(pos[1])))))    
    file = open(str(filename)+".kml","w") 
    file.write(str(parser.etree.tostring(fld)))
    file.close()

def import_kml(filename):
    """ Import KML file by retreiving timestamps and positions """
    out = []
    ts = []
    with open(filename) as f:
        root = parser.parse(f).getroot()
        pms = root.findall('.//{http://www.opengis.net/kml/2.2}Placemark')
        for pm in pms:
            ts.append(float(pm.description.text.replace('\n',' ').split(' ')[1]))
            out.append( np.array(str(pm.findall('.//{http://www.opengis.net/kml/2.2}Point')[0].coordinates).split(',')).astype(np.double))
    for i in range(0,len(out)):
        out[i][0:2] = np.deg2rad(out[i][0:2])
    return ts, np.array(out)

def R(theta):
    """ Return 2D rotation matrix according rbd convention """
    return np.array([[np.cos(theta), np.sin(theta)],[-np.sin(theta), np.cos(theta)]])

def rotation_proj(attitude1, attitude2):
    """ Project the rotation only on Z axis from attitude1 to attitude2 """
    r = attitude1.apply(attitude2.apply([1,0,0],True))
    return rot.from_dcm(np.array([[r[0], -r[1], 0.],[r[1], r[0], 0.],[0., 0., 1.]]))

def rotation_ort(attitude1, attitude2):
    """ Return the complement rotation from the projection on Z axis from attitude1 to attitude2 """
    ort = attitude2*attitude1.inv()*rotation_proj(attitude1, attitude2)
    return ort

def ecef2lla(pos, inv=False):
    """ Convert position in ECEF frame to LLA """
    ecef = pyproj.Proj(proj='geocent', ellps='WGS84', datum='WGS84')
    lla = pyproj.Proj(proj='latlong', ellps='WGS84', datum='WGS84')
    if inv:
        if pos.ndim == 1:
            x, y, z = pyproj.transform(lla, ecef, pos[0], pos[1], pos[2], radians=True)
        else:        
            x, y, z = pyproj.transform(lla, ecef, pos[:,0], pos[:,1], pos[:,2], radians=True)
        return np.array([x, y, z]).T
    else:
        if pos.ndim == 1:
            lon, lat, alt = pyproj.transform(ecef, lla, pos[0], pos[1], pos[2], radians=True)
        else:        
            lon, lat, alt = pyproj.transform(ecef, lla, pos[:,0], pos[:,1], pos[:,2], radians=True)
        return np.array([lon, lat, alt]).T

def ecef2enu(lat0, lon0):
    """ Compute quaternion of transformation between LLA to ENU frame """
    MatNorthPole = np.array([[-1., 0., 0.],
                           [0., -1., 0.],
                           [ 0., 0. , 1.]])
    sColat = np.sin(np.pi/2-lat0)
    cColat = np.cos(np.pi/2-lat0)
    MatLat = np.array([[ cColat , 0. , sColat ],
                     [   0.   , 1. ,   0.   ],
                     [-sColat , 0. , cColat ]])
    sLon = np.sin(lon0)
    cLon = np.cos(lon0)
    Matlon = np.array([[ cLon , -sLon , 0. ],
                     [ sLon , cLon  , 0. ],
                     [  0.  ,  0.   , 1. ]])
    return rot.from_dcm(np.array([[0,-1,0],[1,0,0],[0,0,1]]).dot(Matlon.dot(MatLat.dot(MatNorthPole)).T))

def rbd_translate(gps_positions, attitudes, trans):
    """ Perform a translation in the given frame """
    if not isinstance(attitudes, (np.ndarray, list)):
        return gps_positions + attitudes.apply(trans, True)
    else:          
        car_pos = []
        for i in range(len(gps_positions)):
            car_pos.append(gps_positions[i] + attitudes[i].apply(trans, True))
        return np.array(car_pos)
    
def check_transform(data, rotation, translation, name):
    """ Save an image to vizualize the calculated transformation (for test purpose) """
    translation = translation/0.04
    shape = (np.shape(data.img)[1], np.shape(data.img)[0])
    warp_matrix = np.concatenate(((rotation.as_dcm()[:2,:2]).T,np.array([[-translation[0]],[-translation[1]]])), axis = 1)
    Image.fromarray(cv2.warpAffine(data.img, warp_matrix, shape, flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)).save(name);    

def merge_img(img1, img2, P1, P2):
    """ Merge two images pixel by pixel, weighted by uncertainty, only in modified area """
    img = deepcopy(img1)
    cov_img = deepcopy(P1)
     
    mask1 = np.isnan(img1)
    mask2 = np.isnan(img2)
    
    np.putmask(img, mask1, img2)
    np.putmask(cov_img, mask1, P2)
    np.putmask(img, np.logical_and(np.logical_not(mask1), np.logical_not(mask2)), np.round(np.divide(np.multiply(img1, P2) + np.multiply(img2, P1), P1 + P2)))
    np.putmask(cov_img, np.logical_and(np.logical_not(mask1), np.logical_not(mask2)), np.divide(np.multiply(P1, P2), P1 + P2))
    return img, cov_img
