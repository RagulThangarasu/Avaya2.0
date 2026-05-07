const fs = require('fs');
const path = require('path');
const pdf = require('pdf-parse');

const PROD_DIR = path.join(__dirname, '../PDF/prod');
const STAGE_DIR = path.join(__dirname, '../PDF/stage');

async function debug() {
    const prodFiles = fs.readdirSync(PROD_DIR).filter(f => f.endsWith('.pdf'));
    const stageFiles = fs.readdirSync(STAGE_DIR).filter(f => f.endsWith('.pdf'));

    console.log('--- DEBUG START ---');
    console.log('Files in Prod:', prodFiles);
    console.log('Files in Stage:', stageFiles);

    if (prodFiles.length > 0) {
        const buf = fs.readFileSync(path.join(PROD_DIR, prodFiles[0]));
        const parser = new pdf.PDFParse({ data: buf });
        const result = await parser.getText();
        console.log('Result keys:', Object.keys(result));
        if (result.pages && result.pages.length > 0) {
            console.log('Page 0 keys:', Object.keys(result.pages[0]));
            console.log('Page 0 text preview:', result.pages[0].text.substring(0, 100).replace(/\n/g, ' '));
        }
        console.log(`\nPROD FILE [${prodFiles[0]}] RAW TEXT (First 500 chars):`);
        console.log('--------------------------------------------------');
        console.log(result.text.substring(0, 500));
        console.log('--------------------------------------------------');
    }

    if (stageFiles.length > 0) {
        const buf2 = fs.readFileSync(path.join(STAGE_DIR, stageFiles[1])); // Testing the matching file
        const parser2 = new pdf.PDFParse({ data: buf2 });
        const result2 = await parser2.getText();
        console.log(`\nSTAGE FILE [${stageFiles[1]}] RAW TEXT (First 500 chars):`);
        console.log('--------------------------------------------------');
        console.log(result2.text.substring(0, 500));
        console.log('--------------------------------------------------');
    }
    console.log('--- DEBUG END ---');
}

debug().catch(console.error);
