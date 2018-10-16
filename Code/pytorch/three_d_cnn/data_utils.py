import pandas as pd
import numpy as np
import nibabel as nib
from os import path
from torch.utils.data import Dataset
from sklearn.model_selection import StratifiedShuffleSplit, StratifiedKFold


class MRIDataset(Dataset):
    """labeled Faces in the Wild dataset."""

    def __init__(self, img_dir, data_file, transform=None):
        """
        Args:
            img_dir (string): Directory of all the images.
            data_file (string): File name of the train/test split file.
            transform (callable, optional): Optional transform to be applied on a sample.

        """
        self.img_dir = img_dir
        self.data_file = data_file
        self.transform = transform

        # Check the format of the tsv file here
        df = pd.read_csv(self.data_file, sep='\t')
        if ('diagnosis' not in list(df.columns.values)) or ('session_id' not in list(df.columns.values)) or \
           ('participant_id' not in list(df.columns.values)):
            raise Exception("the data file is not in the correct format."
                            "Columns should include ['participant_id', 'session_id', 'diagnosis']")

    def __len__(self):
        df = pd.read_csv(self.data_file, sep='\t')
        return len(df)

    def __getitem__(self, idx):
        df = pd.read_csv(self.data_file, sep='\t')
        img_list = list(df['participant_id'])
        sess_list = list(df['session_id'])
        label_list = list(df['diagnosis'])

        img_name = img_list[idx]
        img_label = label_list[idx]
        sess_name = sess_list[idx]
        # Not in BIDS but in CAPS
        image_path = path.join(self.img_dir, img_name, sess_name, 'anat',
                                  img_name + '_' + sess_name + '_T1w.nii.gz')
        image = nib.load(image_path)
        samples = []
        if img_label == 'CN':
            label = 0
        elif img_label == 'AD':
            label = 1
        elif img_label == 'MCI':
            label = 2

        AXimageList, n_image_ax = axKeySlice(image)
        CORimageList, n_image_cor = corKeySlice(image)
        SAGimageList, n_image_sag = sagKeySlice(image)

        for img2DList in (AXimageList, CORimageList, SAGimageList):
            for image2D in img2DList:
                if self.transform:
                    image2D = self.transform(image2D)
                sample = {'image': image2D, 'label': label}
                samples.append(sample)
        random.shuffle(samples)
        return samples


def subject_diagnosis_df(subject_session_df):
    """
    Creates a DataFrame with only one occurence of each subject and the most early diagnosis
    Some subjects may not have the baseline diagnosis (ses-M00 doesn't exist)

    :param subject_session_df: (DataFrame) a DataFrame with columns containing 'participant_id', 'session_id', 'diagnosis'
    :return: DataFrame with the same columns as the input
    """
    temp_df = subject_session_df.set_index(['participant_id', 'session_id'])
    subjects_df = pd.DataFrame(columns=subject_session_df.columns)
    for subject, subject_df in temp_df.groupby(level=0):
        session_nb_list = [int(session[5::]) for _, session in subject_df.index.values]
        session_nb_list.sort()
        session_baseline_nb = session_nb_list[0]
        if session_baseline_nb < 10:
            session_baseline = 'ses-M0' + str(session_baseline_nb)
        else:
            session_baseline = 'ses-M' + str(session_baseline_nb)
        row_baseline = list(subject_df.loc[(subject, session_baseline)])
        row_baseline.insert(0, subject)
        row_baseline.insert(1, session_baseline)
        row_baseline = np.array(row_baseline).reshape(1, len(row_baseline))
        row_df = pd.DataFrame(row_baseline, columns=subject_session_df.columns)
        subjects_df = subjects_df.append(row_df)

    subjects_df.reset_index(inplace=True, drop=True)
    return subjects_df


def multiple_time_points(df, subset_df):
    """
    Returns a DataFrame with all the time points of each subject

    :param df: (DataFrame) the reference containing all the time points of all subjects.
    :param subset_df: (DataFrame) the DataFrame containing the subset of subjects.
    :return: mtp_df (DataFrame) a DataFrame with the time points of the subjects of subset_df
    """
    mtp_df = pd.DataFrame(columns=df.columns)
    temp_df = df.set_index('participant_id')
    for idx in subset_df.index.values:
        subject = subset_df.loc[idx, 'participant_id']
        subject_df = temp_df.loc[subject]
        if isinstance(subject_df, pd.Series):
            subject_id = subject_df.name
            row = list(subject_df.values)
            row.insert(0, subject_id)
            subject_df = pd.DataFrame(np.array(row).reshape(1, len(row)), columns=df.columns)
            mtp_df = mtp_df.append(subject_df)
        else:
            mtp_df = mtp_df.append(subject_df.reset_index())

    mtp_df.reset_index(inplace=True, drop=True)
    return mtp_df


def split_subjects_to_tsv(diagnoses_tsv, n_splits=5, val_size=0.15):
    """
    Write the tsv files corresponding to the train/val/test splits of all folds

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param n_splits: (int) the number of splits wanted in the cross-validation
    :param val_size: (float) proportion of the train set being used for validation
    :return: None
    """

    df = pd.io.parsers.read_csv(diagnoses_tsv, sep='\t')
    if 'diagnosis' not in list(df.columns.values):
        raise Exception('Diagnoses file is not in the correct format.')
    # Here we reduce the DataFrame to have only one diagnosis per subject (multiple time points case)
    diagnosis_df = subject_diagnosis_df(df)
    diagnoses_list = list(diagnosis_df.diagnosis)
    unique = list(set(diagnoses_list))
    y = np.array([unique.index(x) for x in diagnoses_list])  # There is one label per diagnosis depending on the order

    splits = StratifiedKFold(n_splits=n_splits, shuffle=True)

    n_iteration = 0
    for train_index, test_index in splits.split(np.zeros(len(y)), y):

        y_train = y[train_index]
        diagnosis_df_train = diagnosis_df.loc[train_index]

        # split the train data into training and validation set
        skf_2 = StratifiedShuffleSplit(n_splits=1, test_size=val_size)
        indices = next(skf_2.split(np.zeros(len(y_train)), y_train))
        train_ind, valid_ind = indices

        # We use only one session per subject in the test set
        df_test = diagnosis_df.iloc[test_index]

        df_sub_valid = diagnosis_df_train.iloc[valid_ind]
        df_sub_train = diagnosis_df_train.iloc[train_ind]
        df_valid = multiple_time_points(df, df_sub_valid)
        df_train = multiple_time_points(df, df_sub_train)

        df_train.to_csv(path.join(path.dirname(diagnoses_tsv), path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(n_iteration) + '_train.tsv'), sep='\t', index=False)
        df_test.to_csv(path.join(path.dirname(diagnoses_tsv), path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(n_iteration) + '_test.tsv'), sep='\t', index=False)
        df_valid.to_csv(path.join(path.dirname(diagnoses_tsv), path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(n_iteration) + '_valid.tsv'), sep='\t', index=False)
        n_iteration += 1


def load_split(diagnoses_tsv, fold):
    """
    Loads the

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param fold: (int) the number of the current fold
    :return: 3 DataFrame
        training_tsv
        test_tsv
        valid_tsv
    """
    training_tsv = path.join(path.dirname(diagnoses_tsv),
                             path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(fold) + '_train.tsv')
    test_tsv = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(fold) + '_train.tsv')
    valid_tsv = path.join(path.dirname(diagnoses_tsv),
                          path.basename(diagnoses_tsv).split('.')[0] + '_iteration-' + str(fold) + '_test.tsv')
    return training_tsv, test_tsv, valid_tsv