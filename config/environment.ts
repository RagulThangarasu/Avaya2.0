import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config();

export const config = {
    baseURL: process.env.BASE_URL || '',
    aemFullURL: process.env.AEM_FULL_URL || '',
    aemEditorURL: process.env.AEM_EDITOR_URL || '',
    contentValidationUrl: process.env.CONTENT_VALIDATION_URL || '',
    referenceUrl: process.env.REFERENCE_URL || '',
    leftNavAemUrl: process.env.LEFTNAV_AEM_URL || '',
    leftNavReferenceUrl: process.env.LEFTNAV_REFERENCE_URL || '',
    leftNavAemSitesUrl: process.env.LEFTNAV_AEMSITES_URL || '',
    credentials: {
        username: process.env.AEM_USERNAME || '',
        password: process.env.AEM_PASSWORD || '',
    },
    session: {
        dir: path.resolve(process.env.SESSION_DIR || './auth-sessions'),
        storageStatePath: path.resolve(
            process.env.SESSION_DIR || './auth-sessions',
            'storage-state.json'
        ),
        cookiesPath: path.resolve(
            process.env.SESSION_DIR || './auth-sessions',
            'cookies.json'
        ),
        maxAgeHours: parseInt(process.env.SESSION_MAX_AGE_HOURS || '12', 10),
    },
    timeouts: {
        navigation: 60_000,
        action: 30_000,
        login: 90_000,
        elementWait: 15_000,
    },
    metadata: {
        avayaExcelPath: path.resolve('./data/Avaya-Metadata-Properties.xlsx'),
        publicationExcelPath: path.resolve('./data/Publication-Metadata.xlsx'),
    },
};
