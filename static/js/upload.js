/**
 * Hub Academy - File Upload Handler
 */

document.addEventListener('DOMContentLoaded', function() {
    initUploadZones();
});

/**
 * Initialize all upload zones
 */
function initUploadZones() {
    const uploadZones = document.querySelectorAll('.upload-zone');

    uploadZones.forEach(function(zone) {
        const input = zone.querySelector('.upload-input');
        const materialType = zone.dataset.type;
        const lessonId = zone.dataset.lesson;

        // Click to upload
        zone.addEventListener('click', function(e) {
            if (e.target !== input) {
                input.click();
            }
        });

        // File input change
        input.addEventListener('change', function() {
            if (this.files.length > 0) {
                uploadFiles(Array.from(this.files), materialType, lessonId);
                this.value = ''; // Reset input
            }
        });

        // Drag and drop
        zone.addEventListener('dragover', function(e) {
            e.preventDefault();
            e.stopPropagation();
            this.classList.add('dragover');
        });

        zone.addEventListener('dragleave', function(e) {
            e.preventDefault();
            e.stopPropagation();
            this.classList.remove('dragover');
        });

        zone.addEventListener('drop', function(e) {
            e.preventDefault();
            e.stopPropagation();
            this.classList.remove('dragover');

            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) {
                uploadFiles(files, materialType, lessonId);
            }
        });
    });
}

/**
 * Upload multiple files
 */
function uploadFiles(files, materialType, lessonId) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

    files.forEach(function(file) {
        uploadFile(file, materialType, lessonId, csrfToken);
    });
}

/**
 * Upload a single file
 */
function uploadFile(file, materialType, lessonId, csrfToken) {
    const formData = new FormData();
    formData.append('file', file);

    // Map display type to actual material type
    let actualType = materialType;
    if (materialType === 'video_file') {
        actualType = 'video';
    }
    formData.append('material_type', actualType);

    // Create progress item - handle special list ID cases
    let listId;
    if (materialType === 'case_study') {
        listId = 'case_studies-list';
    } else if (materialType === 'video_file') {
        listId = 'videos-list';
    } else {
        listId = materialType + 's-list';
    }
    const list = document.getElementById(listId);
    const progressItem = createProgressItem(file.name);
    list.appendChild(progressItem);

    fetch(`/uploads/lessons/${lessonId}/materials`, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfToken
        },
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Replace progress item with actual material item
            const materialItem = createMaterialItem(data.material, materialType);
            list.replaceChild(materialItem, progressItem);
            showNotification(`Uploaded: ${data.material.file_name}`, 'success');
        } else {
            progressItem.remove();
            showNotification(data.error || 'Upload failed', 'error');
        }
    })
    .catch(error => {
        progressItem.remove();
        showNotification('Upload failed: ' + error.message, 'error');
    });
}

/**
 * Create progress item element
 */
function createProgressItem(fileName) {
    const li = document.createElement('li');
    li.className = 'material-item uploading';
    li.innerHTML = `
        <i class="fas fa-spinner fa-spin"></i>
        <span class="material-name">${fileName}</span>
        <span class="text-muted text-sm">Uploading...</span>
    `;
    return li;
}

/**
 * Create material item element
 */
function createMaterialItem(material, materialType) {
    const iconMap = {
        slide: 'fa-file-powerpoint',
        article: 'fa-file-alt',
        case_study: 'fa-briefcase',
        video: 'fa-video',
        video_file: 'fa-video'
    };

    // Map video_file to video for icon lookup
    const iconType = materialType === 'video_file' ? 'video' : materialType;

    const li = document.createElement('li');
    li.className = 'material-item';
    li.dataset.id = material.id;

    // For video files, add a play button
    if (materialType === 'video_file' && material.file_path) {
        li.innerHTML = `
            <i class="fas ${iconMap[iconType]}"></i>
            <span class="material-name">${material.file_name || material.video_name}</span>
            <a href="/uploads/media/${material.file_path}" target="_blank" class="btn btn-ghost btn-icon">
                <i class="fas fa-play"></i>
            </a>
            <button class="btn btn-ghost btn-icon text-danger" onclick="deleteMaterial(${material.id})">
                <i class="fas fa-trash"></i>
            </button>
        `;
    } else {
        li.innerHTML = `
            <i class="fas ${iconMap[iconType]}"></i>
            <span class="material-name">${material.file_name || material.video_name}</span>
            <button class="btn btn-ghost btn-icon text-danger" onclick="deleteMaterial(${material.id})">
                <i class="fas fa-trash"></i>
            </button>
        `;
    }
    return li;
}

/**
 * Update badge count
 */
function updateBadgeCount(materialType, delta) {
    const section = document.querySelector(`[data-type="${materialType}"]`);
    if (section) {
        const badge = section.querySelector('.badge');
        if (badge) {
            const currentCount = parseInt(badge.textContent) || 0;
            badge.textContent = currentCount + delta;
        }
    }
}
